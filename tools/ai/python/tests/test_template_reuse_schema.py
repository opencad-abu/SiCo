"""Record version dispatch, electrical contract counterexamples and legacy guardrails."""

import copy
import sqlite3

import pytest
from sicostate import project_directory
from template_circuit_fixture import persisted_case
from template_reuse_fixture import install_v3, v3_record
from test_template_circuit_transport import NoLiveClient, rpc, unpack

from cadai.mcp import McpServer
from cadai.template_build import publish
from cadai.template_catalog import TemplateCatalog
from cadai.template_circuit import prepare_template
from cadai.template_circuit_map import source_parts
from cadai.template_project import project_record
from cadai.template_reuse_schema import validate_record
from cadai.template_schema import SCHEMA, SCHEMA_V1, TemplateError, TemplateUnavailable, arguments
from cadai.template_symbol_plan import make_plan
from cadai.template_topology import evidence_gaps


@pytest.mark.parametrize("optional", [False, True])
def test_v3_valid_contract_does_not_mutate_or_qualify_record(optional):
    value = v3_record(optional)
    before = copy.deepcopy(value)
    validate_record(value)
    assert value == before
    value["topology"]["gaps"].append({"code": "inherited_connectivity_not_expanded"})
    validate_record(value)
    assert "inherited_connectivity_not_expanded" in evidence_gaps(value["topology"])


@pytest.mark.parametrize(
    "fault",
    [
        "missing_core_pin",
        "absent_core",
        "duplicate_core",
        "extra_field",
        "overlap",
        "unpartitioned",
        "missing_boundary",
        "boundary_range",
        "boundary_kind",
        "duplicate_boundary_net",
        "missing_group_edge",
        "wrong_omitted_net",
        "wrong_omitted_port",
        "dangling_pin",
        "duplicate_device",
        "duplicate_pin",
        "duplicate_rule",
        "unfixed_rule",
        "absent_placement",
        "bad_mirror",
        "missing_spacing_class",
        "missing_routing_asset",
        "unavailable_with_anchors",
        "wrong_granularity",
        "self_base",
    ],
)
def test_invalid_contract_cannot_be_read_as_v3(fault):
    value = v3_record(True)
    c = value["reuse_contract"]
    if fault == "missing_core_pin":
        c["core_connections"].pop()
    elif fault == "absent_core":
        c["core_devices"].append("absent")
    elif fault == "duplicate_core":
        c["core_devices"].append("d0")
    elif fault == "extra_field":
        c["confidence"] = 1
    elif fault == "overlap":
        c["optional_groups"][0]["devices"].append("d0")
    elif fault == "unpartitioned":
        c["optional_groups"] = []
    elif fault == "missing_boundary":
        c["boundary_terminals"] = []
    elif fault == "boundary_range":
        c["boundary_terminals"][0]["max_additional_connections"] = 0
    elif fault == "boundary_kind":
        c["boundary_terminals"][0]["allowed_device_kinds"] = ["inductor"]
    elif fault == "duplicate_boundary_net":
        duplicate = copy.deepcopy(c["boundary_terminals"][0])
        duplicate["endpoint"]["device"] = "d1"
        c["boundary_terminals"].append(duplicate)
    elif fault == "missing_group_edge":
        c["optional_groups"][0]["boundary_connections"] = []
    elif fault == "wrong_omitted_net":
        c["optional_groups"][0]["omit"]["remove_nets"] = []
    elif fault == "wrong_omitted_port":
        c["optional_groups"][0]["omit"]["remove_ports"] = []
    elif fault == "dangling_pin":
        value["topology"]["devices"][0]["pins"][0]["net"] = "absent"
    elif fault == "duplicate_device":
        value["topology"]["devices"].append(value["topology"]["devices"][0])
    elif fault == "duplicate_pin":
        pins = value["topology"]["devices"][0]["pins"]
        pins.append(pins[0])
    elif fault in {"duplicate_rule", "unfixed_rule"}:
        rule = {"id": "omit", "version": "1", "implementation_digest": "0" * 64}
        c["allowed_rule_refs"] = [rule, rule] if fault == "duplicate_rule" else [{"id": "omit"}]
    elif fault in {"absent_placement", "bad_mirror", "missing_spacing_class"}:
        c["placement_constraints"] = [
            {
                "kind": "spacing" if fault == "missing_spacing_class" else "mirror",
                "devices": ["d0", "absent"] if fault == "absent_placement" else ["d0", "d1", "d2"],
                "strength": "hard",
                "axis": "x",
            }
        ]
    elif fault == "missing_routing_asset":
        c["routing_reference"].update(status="captured", anchor_ids=["a0"], segment_ids=["s0"])
    elif fault == "unavailable_with_anchors":
        c["routing_reference"]["anchor_ids"] = ["a0"]
    elif fault == "wrong_granularity":
        value["granularity"] = "L1"
    elif fault == "self_base":
        value["base_template_ref"] = value["template_ref"]
    with pytest.raises(TemplateError):
        validate_record(value)


def test_v3_reader_and_mcp_detail_dispatch(tmp_path):
    value = v3_record()
    root = project_directory(tmp_path, "ai/reference/circuit-templates", create=True)
    install_v3(root, value)
    assert TemplateCatalog(root).get(value["template_ref"]) == value
    server = McpServer(NoLiveClient(), workspace=tmp_path)
    detail = unpack(
        rpc(
            server,
            "get_circuit_template",
            {
                "template_ref": value["template_ref"],
                "section": "reuse_contract",
            },
        )
    )
    assert detail["metadata"]["routing_reference"]["status"] == "unavailable"
    assert detail["total"] == 10  # Two core IDs and eight protected terminals.
    value["reuse_contract"]["core_connections"].pop()
    from cadai.template_schema import canonical

    with sqlite3.connect(str(root / "catalog.sqlite3")) as db:
        db.execute("UPDATE templates SET data_json=?", (canonical(value),))
    with pytest.raises(TemplateUnavailable, match="invalid template v3"):
        TemplateCatalog(root).get(value["template_ref"])


def test_v3_cannot_be_downgraded_or_consumed_before_creation_migration(tmp_path):
    value = v3_record()
    with pytest.raises(TemplateUnavailable, match="legacy consumer"):
        source_parts(value)
    with pytest.raises(TemplateUnavailable, match="legacy consumer"):
        make_plan(value, {})
    with pytest.raises(TemplateError, match="projection"):
        project_record(value, "L1")
    with pytest.raises(TemplateError, match="publication"):
        publish([value], tmp_path / "unpublished.sqlite3")
    assert not (tmp_path / "unpublished.sqlite3").exists()


@pytest.mark.parametrize("version", [SCHEMA_V1, SCHEMA])
def test_legacy_catalog_and_explicit_consumer_keep_references(tmp_path, monkeypatch, version):
    args, value = persisted_case(tmp_path, monkeypatch)
    root = tmp_path / ".cad/ai/reference/circuit-templates"
    path = root / "catalog.sqlite3"
    with sqlite3.connect(str(path)) as db:
        from cadai.template_schema import canonical

        value["schema_version"] = version
        db.execute("UPDATE metadata SET value=? WHERE key='schema_version'", (version,))
        db.execute("UPDATE templates SET data_json=?", (canonical(value),))
    catalog = TemplateCatalog(root)
    assert catalog.get(value["template_ref"]) == value
    result = prepare_template(args, workspace=tmp_path)
    assert result["template_use"]["template_ref"] == value["template_ref"]
    assert result["plan"]["structural_valid"]
    args = arguments(
        "get_circuit_template", {"template_ref": value["template_ref"], "section": "reuse_contract"}
    )
    assert catalog.detail(args)["code"] == "asset_unavailable"
