"""Supply/ground templates adapt without globals; PG hints stay advisory."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from test_circuit_templates import make_catalog

from cadai.circuit_spec_plan import preview_circuit
from cadai.template_catalog import TemplateCatalog
from cadai.template_circuit import prepare_template
from cadai.template_pg import pg_hints
from cadai.template_schema import TemplateUnavailable


def capture_rows(sig_types=None):
    """Two-device reference; VDD/VSS carry supply/ground typing."""
    sig_types = sig_types or {"VDD": "supply", "VSS": "ground", "N1": "signal"}
    rows = [
        {
            "kind": "header",
            "schema_version": 1,
            "status": "raw_capture",
            "lib": "demo",
            "cell": "LDO",  # noqa: S105 - fixture cell name
            "view": "schematic",
            "library_path": "/reference/demo",
            "tool_version": "test",
            "dbu_per_uu": 160,
            "bbox": [[0, 0], [40, 40]],
            "net_global_semantics": "uniform_boolean_v1",
        },
        {
            "kind": "instance",
            "name": "M0",
            "libName": "gpdk045",
            "cellName": "nmos1v",
            "viewName": "symbol",
            "xy": [0, 0],
            "orient": "R0",
            "bbox": [[-5, -5], [5, 5]],
            "terminal_names": ["G", "D", "S", "B"],
            "master_available": True,
        },
        {
            "kind": "instance",
            "name": "M1",
            "libName": "gpdk045",
            "cellName": "pmos1v",
            "viewName": "symbol",
            "xy": [20, 0],
            "orient": "MY",
            "bbox": [[15, -5], [25, 5]],
            "terminal_names": ["G", "D", "S", "B"],
            "master_available": True,
        },
    ]
    for name, terminals in (
        ("M0", {"G": "N1", "D": "N1", "S": "VSS", "B": "VSS"}),
        ("M1", {"G": "N1", "D": "VDD", "S": "VDD", "B": "VDD"}),
    ):
        rows.extend(
            {"kind": "instance_terminal", "instance": name, "name": pin, "net": net}
            for pin, net in terminals.items()
        )
    net_terminals = Counter(
        row["net"] for row in rows if row["kind"] == "instance_terminal"
    )
    rows.extend(
        {
            "kind": "net",
            "name": net,
            "numBits": 1,
            "sigType": sig_type,
            "is_global": False,
            "instance_terminal_count": net_terminals.get(net, 0),
        }
        for net, sig_type in sig_types.items()
    )
    rows.extend(
        [
            {"kind": "terminal", "name": "VDD", "direction": "input", "numBits": 1, "net": "VDD"},
            {
                "kind": "terminal",
                "name": "VSS",
                "direction": "inputOutput",
                "numBits": 1,
                "net": "VSS",
            },
            {"kind": "pin", "terminal": "VDD", "net": "VDD"},
            {"kind": "pin", "terminal": "VSS", "net": "VSS"},
        ]
    )
    counts = Counter(row["kind"] for row in rows)
    rows.append(
        {
            "kind": "footer",
            "complete": True,
            "counts": {
                key: counts[key]
                for key in ("instance", "instance_terminal", "net", "terminal", "pin", "shape")
            },
        }
    )
    return rows


def prepared_case(tmp_path: Path, rows=None, monkeypatch=None):
    if monkeypatch is not None:
        for legacy in ("CAD_CIRCUIT_TEMPLATES_DIR", "CAD_AI_TEMPLATE_ROOT"):
            monkeypatch.delenv(legacy, raising=False)
    root = tmp_path / ".cad/ai/reference/circuit-templates"
    root.mkdir(parents=True)
    make_catalog(root, rows or capture_rows())
    catalog = TemplateCatalog(root)
    record = catalog.get(catalog.query({"offset": 0, "limit": 1})["templates"][0]["template_ref"])
    masters, devices = [], []
    for index, source in enumerate(record["topology"]["devices"]):
        master_id = "m" + str(index)
        masters.append(
            {
                "id": master_id,
                "kind": "device",
                "target": {"library": "work", "cell": "dev_" + str(index), "view": "symbol"},
                "library_path": "/work",
                "revision": "rev1",
                "terminals": [
                    {"name": pin["name"], "direction": "inputOutput"}
                    for pin in source["pins"]
                ],
                "terminals_complete": True,
                "parameters_complete": True,
                "parameters": [{"name": "w", "type": "string", "editable": True}],
                "callbacks": {"status": "none"},
            }
        )
        devices.append(
            {
                "source_device": source["id"],
                "instance": "I" + str(index),
                "name": "I" + str(index),
                "master": master_id,
                "parameters": {"w": "1u"},
            }
        )
    return {
        "template_ref": record["template_ref"],
        "target": {"library": "work_lib", "cell": "ldo_copy", "view": "schematic"},
        "bindings": {
            "schema": "cad.circuit.bindings.v1",
            "project_ref": "fixture:pg",
            "snapshot_ref": "fixture:pg",
            "source_ref": "fixture",
            "evidence_kind": "test_fixture",
            "masters": masters,
        },
        "devices": devices,
        "net_map": {},
        "port_map": {},
    }


def test_supply_and_ground_nets_adapt_as_local_nets(tmp_path, monkeypatch):
    args = prepared_case(tmp_path, monkeypatch=monkeypatch)
    prepared = prepare_template(args, workspace=tmp_path)
    spec = prepared["spec"]
    assert {net["name"]: net["scope"] for net in spec["nets"]} == {
        "VDD": "local",
        "VSS": "local",
        "N1": "local",
    }
    assert sorted(port["name"] for port in spec["ports"]) == ["VDD", "VSS"]
    # the spec still validates end to end, and PG hints stay advisory
    preview_circuit(spec, args["bindings"])
    hints = {row["net"]: row for row in prepared["pg_hints"]["nets"]}
    assert hints["VDD"]["role_hint"] == "power" and hints["VDD"]["confidence"] == "high"
    assert hints["VSS"]["role_hint"] == "ground" and hints["VSS"]["confidence"] == "high"
    assert "N1" not in hints  # plain signal nets stay out of the candidate list
    assert "hint_only" in prepared["pg_hints"]["use"]
    placement_hints = prepared["plan"]["placement"]["pg_hints"]
    assert placement_hints["schema"] == "cad.template.pg_hints.v1"


def test_name_evidence_fixes_misclassified_signal_nets():
    """LDO_INV-style capture: ``in`` typed supply must not become a PG pin."""
    record = {"template_ref": "tpl_" + "0" * 64, "topology": None, "assets": {}}
    # minimal topology shaped like the captured LDO_INV record
    record["topology"] = {
        "devices": [
            {"id": "d0", "kind": "nmos", "pins": [{"name": "G", "net": "n2"}]},
        ],
        "nets": [
            {"id": "n0", "source_name": "VDD", "sig_type": "supply", "is_global": False},
            {"id": "n1", "source_name": "VSS", "sig_type": "ground", "is_global": False},
            {"id": "n2", "source_name": "in", "sig_type": "supply", "is_global": False},
        ],
        "ports": [
            {"id": "p0", "name": "VDD", "direction": "input", "net": "n0"},
            {"id": "p1", "name": "in", "direction": "input", "net": "n2"},
            {"id": "p2", "name": "VSS", "direction": "inputOutput", "net": "n1"},
        ],
    }
    hints = {row["net"]: row for row in pg_hints(record)["nets"]}
    assert hints["VDD"]["role_hint"] == "power"
    assert hints["VSS"]["role_hint"] == "ground"
    assert hints["in"]["role_hint"] == "signal"
    assert hints["in"]["confidence"] == "low"
    assert "pg_sig_type_without_pg_name" in hints["in"]["reasons"]


def test_unknown_sig_type_is_still_refused(tmp_path, monkeypatch):
    rows = capture_rows()
    for row in rows:
        if row.get("kind") == "net" and row["name"] == "N1":
            row["sigType"] = "wired_or"
    args = prepared_case(tmp_path, rows=rows, monkeypatch=monkeypatch)
    with pytest.raises(TemplateUnavailable) as excinfo:
        prepare_template(args, workspace=tmp_path)
    assert "wired_or" in str(excinfo.value)


def test_pg_hints_require_topology():
    with pytest.raises(TemplateUnavailable):
        pg_hints({"template_ref": "tpl_x", "topology": None})
