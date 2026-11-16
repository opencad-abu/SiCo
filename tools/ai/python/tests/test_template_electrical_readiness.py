"""Extraction must not qualify a graph the authoritative consumer already rejects."""

import copy
from pathlib import Path

import pytest
from template_extract_fixture import capture_rows, recount, request
from test_circuit_templates import write_capture
from test_template_extract_reuse import extract

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_extract_reuse import extract_reuse
from cadai.template_reuse_build import content_ref, make_reusable, publish_reusable
from cadai.template_schema import TemplateError
from cadai.template_source_qualification import qualification_input, require_qualified
from cadai.template_topology import evidence_gaps


def fault_rows(fault):
    rows = capture_rows()
    device = next(r for r in rows if r["kind"] == "instance_terminal")
    port = next(r for r in rows if r["kind"] == "terminal")
    net = next(r for r in rows if r["kind"] == "net")
    if fault == "open_device":
        device["net"] = None
    elif fault == "omitted_device_endpoint":
        rows.remove(device)
    elif fault == "open_port":
        port["net"] = None
        next(r for r in rows if r["kind"] == "pin")["net"] = None
    elif fault == "port_direction":
        port["direction"] = "unknown"
    elif fault == "port_width":
        port["numBits"] = net["numBits"] = 2
    elif fault == "inconsistent_width":
        port["numBits"] = 2
    elif fault == "signal_type":
        net["sigType"] = None
    elif fault == "missing_scope":
        net.pop("is_global")
    elif fault == "missing_width":
        net.pop("numBits")
    elif fault == "missing_connection":
        device.pop("net")
    elif fault == "empty_inventory":
        rows[2]["terminal_names"] = []
        next(r for r in rows if r["kind"] == "instance_geometry")["observation"] = dict(
            ok=False, code="missing", message="empty master")
    elif fault == "pin_disagrees":
        next(r for r in rows if r["kind"] == "pin")["net"] = "Y"
    for row in rows:
        if row["kind"] == "net":
            row["instance_terminal_count"] = sum(
                r["kind"] == "instance_terminal" and r.get("net") == row["name"] for r in rows)
    return recount(rows)


@pytest.mark.parametrize("fault,code,kind", [
    ("open_device", "unconnected_endpoint", "device"),
    ("omitted_device_endpoint", "unconnected_endpoint", "device"),
    ("open_port", "unconnected_endpoint", "port"),
    ("port_direction", "unknown_port_direction", "port"),
    ("port_width", "bus_not_expanded", "port"),
    ("signal_type", "unknown_signal_type", "net"),
])
def test_public_extract_retains_reference_with_electrical_issue(tmp_path, fault, code, kind):
    client, _, result = extract(tmp_path, rows=fault_rows(fault))
    assert not result["ok"] and result["stage"] == "stored_reference", result
    assert result["reuse"]["status"] == "unavailable"
    assert result["reuse"]["template_ref"] is None
    issue = next(i for i in result["reuse"]["issues"]
                 if i["code"] == code and i.get("object_kind") == kind)
    assert issue["object_kind"] == kind and issue["object_ref"]
    if kind == "device":
        assert issue["object_ref"] == "M0" and issue["terminal"] == "G"
    assert issue["next_action"]
    record = TemplateCatalog(workspace=tmp_path).get(result["template_ref"])
    assert record["schema_version"] == "cad.circuit.template.v2"
    assert code in evidence_gaps(record["topology"])
    assert Path(result["capture"]).read_bytes() == client.path.read_bytes()


@pytest.mark.parametrize("fault", ["missing_scope", "missing_width", "missing_connection",
                                  "empty_inventory", "pin_disagrees", "inconsistent_width"])
def test_incomplete_or_contradictory_capture_is_rejected_before_publication(tmp_path, fault):
    from template_extract_fixture import CaptureClient

    client = CaptureClient(tmp_path / "spool", fault_rows(fault))
    with pytest.raises(TemplateError):
        call_template("extract_circuit_templates", request(), workspace=tmp_path, client=client)
    assert not list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.parametrize("fault", ["open_device", "port_direction", "port_width", "signal_type"])
def test_private_builder_cannot_bypass_consumer_electrical_requirements(tmp_path, fault):
    raw = read_capture(write_capture(tmp_path, capture_rows()))
    base = make_template(raw, classifications=request()["classifications"])
    qualified, result = extract_reuse(base, request()["reuse"])
    assert result["status"] == "qualified"
    bad = make_template(read_capture(write_capture(tmp_path, fault_rows(fault), "bad.jsonl")),
                        classifications=request()["classifications"])
    before = copy.deepcopy(bad)
    with pytest.raises(TemplateError):
        make_reusable(bad, qualified["reuse_contract"],
                      {d["id"]: d["classification_evidence"] for d in bad["topology"]["devices"]})
    assert bad == before


def test_recomputed_digests_do_not_make_an_open_endpoint_qualified(tmp_path):
    from cadai.template_schema import digest

    raw = read_capture(write_capture(tmp_path, capture_rows()))
    good, _ = extract_reuse(make_template(raw, classifications=request()["classifications"]),
                         request()["reuse"])
    good["topology"]["devices"][0]["pins"][0]["net"] = None
    proof = good["source"]["reuse_qualification"]
    proof["input_digest"] = digest(qualification_input(good))
    good["template_ref"] = content_ref(good)
    with pytest.raises(TemplateError, match="unconnected_endpoint"):
        require_qualified(good)
    with pytest.raises(TemplateError, match="unconnected_endpoint"):
        publish_reusable([good], tmp_path / "invalid.sqlite3")
    assert not (tmp_path / "invalid.sqlite3").exists()


def test_missing_classification_reports_source_failures_in_same_response(tmp_path):
    rows, args = fault_rows("open_device"), request()
    args["classifications"].pop("M1")
    rows[1]["electrical_observation"]["views"][0]["terminal_expressions"] = ["hidden!"]
    _, _, result = extract(tmp_path, rows=rows, args=args)
    codes = {i["code"] for i in result["reuse"]["issues"]}
    assert {"classification_required", "unconnected_endpoint",
            "inherited_connections_not_resolved"} <= codes
    assert result["reuse"]["status"] == "unavailable"
    assert "continuation" not in result


def test_bounded_feedback_prioritizes_source_repair_over_classification(tmp_path):
    rows, args = fault_rows("open_device"), request()
    args["classifications"] = {}
    geometry = next(r for r in rows if r["kind"] == "instance_geometry")
    for index in range(10):
        name = "extra" + str(index)
        rows[-2:-2] = [dict(rows[2], name=name), dict(geometry, instance=name)]
    rows[1]["electrical_observation"]["views"][0]["instance_count"] += 10
    _, _, result = extract(tmp_path, rows=recount(rows), args=args)
    assert result["reuse"]["status"] == "unavailable"
    assert len(result["reuse"]["issues"]) == 8
    assert result["reuse"]["issue_count"] > 8
    assert result["reuse"]["issues"][0]["code"] == "unconnected_endpoint"
    assert "continuation" not in result


def test_saved_completion_cannot_repair_electrical_source_with_classification(tmp_path):
    client, _, result = extract(tmp_path, rows=fault_rows("open_device"))
    before = (Path(result["package"]) / "package.json").read_bytes()
    completed = call_template("extract_circuit_templates", dict(
        template_ref=result["template_ref"], reuse=request()["reuse"]), workspace=tmp_path)
    assert not completed["ok"] and completed["live"] is False
    assert completed["template_ref"] == result["template_ref"]
    assert completed["reuse"]["issues"][0]["code"] == "unconnected_endpoint"
    assert (Path(result["package"]) / "package.json").read_bytes() == before
    assert len(client.calls) == 1


def test_current_missing_inventory_rejected_independent_of_row_order(tmp_path):
    rows = fault_rows("missing_connection")
    rows[2:-2] = list(reversed(rows[2:-2]))
    with pytest.raises(TemplateError, match="incomplete electrical observation"):
        read_capture(write_capture(tmp_path, rows))


def test_legacy_missing_scope_keeps_frozen_reconstruction(tmp_path):
    from test_circuit_templates import legacy_rows

    rows = legacy_rows()
    next(r for r in rows if r["kind"] == "net").pop("is_global")
    capture = read_capture(write_capture(tmp_path, rows))
    first = make_template(capture)
    assert first["topology"]["nets"][0]["is_global"] is False
    assert make_template(copy.deepcopy(capture)) == first
