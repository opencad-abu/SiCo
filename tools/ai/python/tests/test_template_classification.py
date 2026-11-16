"""New capture type evidence is independent of project names; archives stay immutable."""

import copy
import hashlib

import pytest
from template_adapt_fixture import reusable
from test_circuit_templates import legacy_rows, write_capture

from cadai.template_build import make_template, publish
from cadai.template_capture import read_capture
from cadai.template_catalog import TemplateCatalog
from cadai.template_classification import RULE_VERSION, SEMANTICS, classify
from cadai.template_graph import normalize_schematic
from cadai.template_reuse_build import make_reusable
from cadai.template_schema import TemplateError, arguments


def observed_rows():
    rows = legacy_rows()
    rows[0]["classification_semantics"] = SEMANTICS
    for row in rows:
        if row["kind"] == "instance":
            row.update(terminal_names=["D", "G"], schematic_available=False,
                       master_oa_view_type="schematicSymbol",
                       builtin_library=None)
    return rows


def evidence(kind="nmos"):
    return dict(kind=kind, attributes={}, source_ref="project:reviewed-device", revision="r1")


@pytest.mark.parametrize("library", ["gpdk045", "smic28hkmg", "gsclib045", "saradc",
                                     "saradcII", "zambezi45", "amsLDO", "US_8ths", "customPDK"])
def test_names_do_not_classify_or_hide_new_source_instances(library):
    rows = observed_rows()
    for row in rows:
        if row["kind"] == "instance":
            row["libName"] = library
    top, _ = normalize_schematic(rows[0], rows[1:-1])
    assert len(top["devices"]) == 2
    assert {row["kind"] for row in top["devices"]} == {"unknown"}
    assert not top["ignored_graphics"]
    assert sum(gap["code"] == "unknown_device_type" for gap in top["gaps"]) == 2
    typed, _ = normalize_schematic(rows[0], rows[1:-1], {"M0": evidence(), "M1": evidence("pmos")})
    assert [row["kind"] for row in typed["devices"]] == ["nmos", "pmos"]
    assert typed["rule_version"] == RULE_VERSION
    assert all("classification_evidence" in row for row in typed["devices"])


def test_builtin_identity_requires_observation_and_accepts_alias():
    row = observed_rows()[1] | {"libName": "analogLib", "cellName": "res"}
    assert classify(row, {"D", "G"})[0] == "unknown"
    row.update(libName="renamedBuiltin", builtin_library="analogLib")
    assert classify(row, {"D", "G"})[1] == "resistor"
    with pytest.raises(TemplateError, match="conflicts"):
        classify(row, {"D", "G"}, evidence())
    row.update(builtin_library="basic", cellName="ipin")
    assert classify(row, set())[0] == "port_graphic"
    with pytest.raises(TemplateError, match="graphic"):
        classify(row, set(), evidence())


def test_explicit_evidence_classifies_a_constructed_device_with_a_schematic():
    """A fab-built device (for example pmos plus diode) keeps its device class."""
    rows, classes = observed_rows(), {"M0": evidence()}
    rows[1]["schematic_available"] = True
    role, kind, _ = classify(rows[1], {"D", "G"}, classes["M0"])
    assert (role, kind) == ("device", evidence()["kind"])
    # Without evidence the same master stays a subcircuit reference.
    assert classify(rows[1], {"D", "G"})[:2] == ("hierarchy", "subcircuit")
    # A label that does not name an electrical device cannot promote it.
    weak = dict(classes["M0"], kind="unknown")
    with pytest.raises(TemplateError, match="electrical device"):
        classify(rows[1], {"D", "G"}, weak)


@pytest.mark.parametrize("fault", ["master", "terminals", "source_ref",
                                  "absent", "unknown", "future", "old"])
def test_incomplete_or_conflicting_evidence_is_rejected(fault):
    rows, classes = observed_rows(), {"M0": evidence()}
    if fault == "master":
        rows[1]["master_available"] = False
    elif fault == "terminals":
        rows[1].pop("terminal_names")
    elif fault == "source_ref":
        classes["M0"].pop("source_ref")
    elif fault == "absent":
        classes["absent"] = classes.pop("M0")
    elif fault == "unknown":
        classes["M0"]["kind"] = "unknown"
    elif fault == "future":
        rows[0]["classification_semantics"] = "future"
    else:
        rows[0].pop("classification_semantics")
    with pytest.raises(TemplateError):
        normalize_schematic(rows[0], rows[1:-1], classes)


def test_new_rules_and_evidence_create_new_refs_without_rewriting_legacy(tmp_path):
    old = make_template(read_capture(write_capture(tmp_path, legacy_rows(), "old.jsonl")))
    raw = read_capture(write_capture(tmp_path, observed_rows(), "new.jsonl"))
    unknown = make_template(raw)
    typed = make_template(raw, classifications={"M0": evidence(), "M1": evidence("pmos")})
    assert old["topology"]["devices"][0]["kind"] == "nmos"
    assert old["rule_version"] == "20260915.2"
    assert unknown["rule_version"] == typed["rule_version"] == RULE_VERSION
    assert len({item["template_ref"] for item in (old, unknown, typed)}) == 3
    assert unknown["capture_sha256"] == typed["capture_sha256"]
    path = tmp_path / "catalog.sqlite3"
    publish([old, unknown, typed], path)
    catalog = TemplateCatalog(tmp_path)
    for item in (old, unknown, typed):
        assert catalog.get(item["template_ref"]) == item
    assert make_template(read_capture(tmp_path / "old.jsonl")) == old


def test_public_extract_evidence_reuses_master_classification_contract():
    args = arguments("extract_circuit_templates", dict(library="work", cell="dut",
                                                       classifications={"M0": evidence()}))
    assert args["classifications"]["M0"] == evidence()
    with pytest.raises(TemplateError):
        arguments("extract_circuit_templates", dict(library="work", cell="dut",
                  classifications={"M0": {"kind": "nmos"}}))


@pytest.mark.parametrize("value", [[], {}, None, float("nan")])
def test_public_evidence_attribute_values_use_authoritative_validator(value):
    item = evidence()
    item["attributes"]["length"] = value
    with pytest.raises(TemplateError):
        arguments("extract_circuit_templates", dict(library="work", cell="dut",
                  classifications={"M0": item}))


def test_reviewed_v3_builder_resolves_only_corresponding_unknown_type_gaps():
    base = reusable()
    classes = {d["id"]: copy.deepcopy(d["classification_evidence"])
               for d in base["topology"]["devices"]}
    for device in base["topology"]["devices"]:
        device.update(role="unknown", kind="unknown")
        device.pop("classification_evidence")
        base["topology"]["gaps"].append(dict(code="unknown_device_type",
                                             instance=device["source_name"]))
    resolved = make_reusable(base, base["reuse_contract"], classes)
    assert not any(g["code"] == "unknown_device_type" for g in resolved["topology"]["gaps"])
    base["topology"]["gaps"].append(dict(code="missing_master", instance="M0"))
    with pytest.raises(TemplateError, match="missing_master"):
        make_reusable(base, base["reuse_contract"], classes)


@pytest.mark.parametrize("value", ["gpdk045", {}, [], True])
def test_invalid_builtin_observation_rejected_before_normalization(tmp_path, value):
    rows = observed_rows()
    rows[1]["builtin_library"] = value
    with pytest.raises(TemplateError, match="builtin library observation"):
        read_capture(write_capture(tmp_path, rows))


def test_classification_evidence_does_not_alias_callers_input():
    rows, classes = observed_rows(), {"M0": evidence()}
    top, _ = normalize_schematic(rows[0], rows[1:-1], classes)
    classes["M0"]["attributes"]["mutated"] = True
    assert top["devices"][0]["classification_evidence"] == evidence()
    assert top["devices"][0]["attributes"] == {}


def test_live_import_rejects_legacy_collector_but_offline_import_remains_readable(tmp_path):
    spool = tmp_path / "spool"
    spool.mkdir()
    path = write_capture(spool, legacy_rows())
    raw = path.read_bytes()
    artifact = dict(path=str(path), size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    catalog = TemplateCatalog(tmp_path / "catalog")
    args = arguments("extract_circuit_templates", dict(library="demo", cell="INV",
                                                       assets=["schematic"]))
    with pytest.raises(TemplateError, match="reload collector"):
        catalog.import_capture(artifact, spool, args)
    assert catalog.import_capture(artifact, spool)["ok"]


@pytest.mark.parametrize("captured_evidence", [True, False])
def test_v3_builder_cannot_override_new_capture_type_evidence(captured_evidence):
    base = reusable()
    base["source"]["classification_semantics"] = SEMANTICS
    classes = {d["id"]: copy.deepcopy(d["classification_evidence"])
               for d in base["topology"]["devices"]}
    classes["d0"]["kind"] = "capacitor"
    if not captured_evidence:
        base["topology"]["devices"][0].pop("classification_evidence")
    with pytest.raises(TemplateError, match="conflicts"):
        make_reusable(base, base["reuse_contract"], classes)
