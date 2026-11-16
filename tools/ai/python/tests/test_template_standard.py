"""Public flat collection integrity, identity, pagination, and transaction boundaries."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from template_extract_fixture import CaptureClient, capture_rows, request

from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_schema import TemplateError
from cadai.template_standard.codec import LIMIT, atomic, jcs, read, sha
from cadai.template_standard.identity import reference
from cadai.template_standard.package import load, publish
from cadai.template_standard.reader import locate
from cadai.template_standard.shards import Writer, mapping


def collected(tmp_path, args=None):
    client = CaptureClient(tmp_path / "spool", capture_rows())
    result = call_template(
        "extract_circuit_templates", args or request(), workspace=tmp_path, client=client
    )
    catalog = TemplateCatalog(workspace=tmp_path)
    item, _ = locate(catalog, result["template_ref"])
    return result, catalog, item


def test_flat_graph_single_connectivity_and_evidence_closure(tmp_path):
    result, catalog, item = collected(tmp_path)
    top = item["documents"]["topology"]
    assert all("net" not in row for row in top["ports"].values())
    assert all("pins" not in row for row in top["devices"].values())
    assert len(top["connections"]) == 6
    assert all(set(row) == {"dependency"} for row in top["masters"].values())
    assert "core_connections" not in item["documents"]["reuse"]
    assert result["capabilities"]["topology-v1"] == "passed"
    assert result["capabilities"]["routing-reference-v1"] == "unknown"
    assert not list(catalog.root.rglob("*.sqlite3"))
    for name in item["package"]["files"]:
        raw = (item["path"] / name).read_bytes()
        assert len(raw) <= LIMIT and sha(raw) == item["package"]["files"][name]
        assert "/pdk" not in raw.decode()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":2}',
        b'{"a":null}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":-0.0}',
        b'{"a":9007199254740992}',
        b"\xef\xbb\xbf{}",
    ],
)
def test_strict_json(tmp_path, raw):
    path = tmp_path / "bad.json"
    path.write_bytes(raw)
    with pytest.raises((TemplateError, ValueError)):
        read(path)


def test_jcs_is_ecmascript_number_and_utf16_key_order():
    assert jcs({"z": 1.0, "a": 1e-6, "b": 1e21}) == b'{"a":0.000001,"b":1e+21,"z":1}'
    assert jcs({"\ufffd": 1, "\U0001f600": 2}).decode().startswith('{"\U0001f600"')


def test_identity_ignores_physical_directory_revision_and_qualification(tmp_path):
    result, _, item = collected(tmp_path)
    package, tables, docs = (
        copy.deepcopy(item["package"]),
        copy.deepcopy(item["tables"]),
        copy.deepcopy(item["documents"]),
    )
    package.update(revision="different", created_at="2030-01-01T00:00:00Z", files={})
    tables["template"]["items"]["main"]["dir"] = "elsewhere"
    docs["qualification"]["checks"]["routing-reference-v1"] = dict(
        result="unknown", reason="Different receipt"
    )
    assert reference(package, tables, docs, "main") == result["template_ref"]
    docs["topology"]["nets"]["n0"]["name"] = "changed"
    assert reference(package, tables, docs, "main") != result["template_ref"]


def test_shards_roundtrip_and_tampering(tmp_path):
    value = {f"n{i:06d}": dict(name="net" + str(i), description="x" * 100) for i in range(5000)}
    writer = Writer(tmp_path)
    writer.put("topology.json", dict(devices=value), "topology")
    stored, _ = read(tmp_path / "topology.json")
    observed = {}
    decoded = mapping(
        tmp_path, stored["devices"], "topology.json", lambda p, d: observed.update({p: d})
    )
    assert decoded == value and len(observed) > 12
    assert all(p.stat().st_size <= LIMIT for p in tmp_path.iterdir())
    name = next(iter(observed))
    (tmp_path / name).write_text("{}")
    with pytest.raises(TemplateError, match="digest mismatch"):
        mapping(tmp_path, stored["devices"], "topology.json", lambda *_: None)


def test_medium_maps_combined_over_limit_are_sharded(tmp_path):
    values = {str(i): "x" * 100 for i in range(60)}
    Writer(tmp_path).put(
        "topology.json", dict(devices=values, nets=values, ports=values), "topology"
    )
    assert (tmp_path / "topology.json").stat().st_size <= LIMIT


@pytest.mark.parametrize("fault", ["missing", "outside", "digest", "endpoint", "reference"])
def test_package_rejects_corrupt_content(tmp_path, fault):
    _, _, item = collected(tmp_path)
    path = item["path"] / "template/topology.json"
    if fault == "missing":
        path.unlink()
    elif fault == "outside":
        raw = path.read_bytes()
        outside = tmp_path / "outside.json"
        outside.write_bytes(raw)
        path.unlink()
        path.symlink_to(outside)
    elif fault == "digest":
        path.write_text("{}")
    else:
        top = json.loads(path.read_bytes())
        if fault == "endpoint":
            top["connections"]["duplicate"] = top["connections"]["c0"]
        else:
            top["nets"]["n0"]["name"] = "modified"
        digest = atomic(path, top)
        manifest = json.loads((item["path"] / "package.json").read_bytes())
        manifest["files"]["template/topology.json"] = digest
        atomic(item["path"] / "package.json", manifest)
    with pytest.raises((TemplateError, OSError)):
        load(item["path"])


def test_reading_without_retained_raw_and_cursor_binding(tmp_path):
    result, catalog, _ = collected(tmp_path)
    Path(result["capture"]).unlink()
    args = dict(
        template_ref=result["template_ref"], section="topology", entity="connections", limit=2
    )
    first = call_template("get_circuit_template", args, workspace=tmp_path)
    assert first["returned"] == 2 and not first["complete"]
    second = call_template(
        "get_circuit_template", args | dict(cursor=first["next_cursor"]), workspace=tmp_path
    )
    assert {r["id"] for r in first["items"]}.isdisjoint(r["id"] for r in second["items"])
    with pytest.raises(TemplateError, match="stale cursor"):
        call_template(
            "get_circuit_template",
            args | dict(entity="devices", cursor=first["next_cursor"]),
            workspace=tmp_path,
        )
    index = catalog.root / "index.json"
    value = json.loads(index.read_bytes())
    value["packages"]["other"] = next(iter(value["packages"].values()))
    atomic(index, value)
    with pytest.raises(TemplateError, match="stale cursor"):
        call_template(
            "get_circuit_template", args | dict(cursor=first["next_cursor"]), workspace=tmp_path
        )


def test_unknown_reference_is_investigable_but_not_design_candidate(tmp_path):
    args = request()
    args.pop("classifications")
    result, _, _ = collected(tmp_path, args)
    assert not result["ok"] and result["stage"] == "stored_reference"
    # The default search also covers the bundled corpus, so scope both queries to
    # the workspace's own tier before asserting what this capture contributed.
    assert call_template(
        "query_circuit_templates", dict(tier="private"), workspace=tmp_path
    )["items"] == []
    query = call_template(
        "query_circuit_templates", dict(purpose="investigate", tier="private"),
        workspace=tmp_path,
    )
    assert [row["template_ref"] for row in query["items"]] == [result["template_ref"]]


def test_failed_publication_keeps_index_and_private_checkpoint(tmp_path, monkeypatch):
    result, catalog, item = collected(tmp_path)
    before = (catalog.root / "index.json").read_bytes()
    tables = copy.deepcopy(item["tables"])
    tables["template"]["items"]["main"]["description"] = "Changed without new identity"
    with pytest.raises(TemplateError, match="immutable_reference_conflict"):
        publish(catalog.root, item["package"], tables, {"main": item["documents"]})
    assert (catalog.root / "index.json").read_bytes() == before
    assert Path(result["capture"]).exists()
    assert list(catalog.root.glob(".collect-*"))


def test_concurrent_retry_publishes_one_revision(tmp_path):
    from cadai.template_capture import parse_capture
    from cadai.template_schema import arguments
    from cadai.template_standard.collect import collect

    client = CaptureClient(tmp_path / "spool", capture_rows())
    raw = client.path.read_bytes()
    options = arguments("extract_circuit_templates", request())

    def run(_):
        return collect(TemplateCatalog(workspace=tmp_path), parse_capture(raw), raw, options)

    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(run, range(3)))
    assert len({r["template_ref"] for r in results}) == 1
    root = TemplateCatalog(workspace=tmp_path).root
    installed = json.loads((root / "index.json").read_bytes())
    assert len(installed["packages"]) == 1


def test_source_literals_are_observations_not_executable_defaults():
    from cadai.template_standard.parameters import literal

    assert literal(dict(type="int", value_skill="12")) == ("integer", 12)
    assert literal(dict(type="float", value_skill="1e-6")) == ("number", 1e-6)
    assert literal(dict(type="string", value_skill='"1u"')) == ("string", "1u")
    for raw in ('pPar("w")', '"pPar(w)"', '"(progn evil)"', "NaN"):
        kind, value = literal(dict(type="string", value_skill=raw))
        assert kind is None and value["state"] == "unknown"


def test_unconfirmed_legacy_import_does_not_invent_permission(tmp_path):
    from test_circuit_templates import legacy_rows, write_capture

    from cadai.template_capture import parse_capture
    from cadai.template_standard.collect import collect

    raw = write_capture(tmp_path, legacy_rows()).read_bytes()
    catalog = TemplateCatalog(root=tmp_path / "catalog")
    result = collect(catalog, parse_capture(raw), raw, {})
    item, _ = locate(catalog, result["template_ref"])
    assert item["entry"]["use"] == dict(reference="unknown", reuse="unknown")


def test_private_evidence_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    catalog = TemplateCatalog(root=tmp_path / "catalog")
    catalog.root.mkdir()
    (catalog.root / "evidence").symlink_to(outside, target_is_directory=True)
    from cadai.template_standard.collect import private_bytes

    with pytest.raises(TemplateError, match="symlink escapes"):
        private_bytes(catalog.root, "evidence", ".json", b"{}")
    assert list(outside.iterdir()) == []


def test_exact_graph_match_survives_private_capture_removal(tmp_path):
    result, _, _ = collected(tmp_path)
    Path(result["capture"]).unlink()
    matched = call_template(
        "match_circuit_template",
        dict(template_ref=result["template_ref"], target_ref=result["template_ref"]),
        workspace=tmp_path,
    )
    assert matched["matched"]


def test_direct_creation_cannot_bypass_unknown_reuse_permission(tmp_path):
    from cadai.template_standard.replay import engine_record, require_execution

    args = request()
    args.pop("reuse")
    _, _, item = collected(tmp_path, args)
    record = engine_record(item)
    with pytest.raises(TemplateError, match="qualified placement"):
        require_execution(record)


def test_same_reference_keeps_selected_tier_without_permission_guess(tmp_path, monkeypatch):
    from cadai.template_locations import TemplateLocation
    from cadai.template_schema import arguments
    from cadai.template_standard.reader import query

    result, catalog, _ = collected(tmp_path)
    catalog.locations = [
        TemplateLocation("private", catalog.root),
        TemplateLocation("project", catalog.root),
    ]
    out = query(catalog, arguments("query_circuit_templates", dict(tier="project")))
    assert out["items"][0]["template_ref"] == result["template_ref"]
    assert out["items"][0]["origin"]["tier"] == "project"
    assert len(out["items"][0]["available_from"]) == 2
