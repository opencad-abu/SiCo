"""Persisted synthetic references and two explicitly incompatible project selections."""

from collections import Counter

from test_circuit_templates import legacy_rows, make_catalog

from cadai.circuit_spec_schema import digest
from cadai.template_catalog import TemplateCatalog


def reference_rows():
    rows = legacy_rows()[:-1]
    rows[0].update(
        net_global_semantics="uniform_boolean_v1",
        incomplete_features=["inherited_connectivity_not_expanded"],
    )
    for row in rows:
        if row["kind"] == "instance":
            x, y = row["xy"]
            row.update(
                bbox=[[x - 0.5, y - 0.5], [x + 0.5, y + 0.5]], terminal_names=["G", "D", "S", "B"]
            )
            rows.extend(
                [
                    {
                        "kind": "instance_terminal",
                        "instance": row["name"],
                        "name": term,
                        "net": "vdd!" if row["name"] == "M1" else "gnd!",
                    }
                    for term in ("S", "B")
                ]
            )
            rows.append(
                {
                    "kind": "instance_property",
                    "instance": row["name"],
                    "name": "w",
                    "type": "string",
                    "value_skill": '"SOURCE_WIDTH * 99"',
                }
            )
            rows.append(
                {
                    "kind": "instance_property",
                    "instance": row["name"],
                    "name": "nf",
                    "type": "string",
                    "value_skill": '"SOURCE_FINGERS"',
                }
            )
    for name, port in [("gnd!", "VSS"), ("vdd!", "VDD")]:
        rows.append(
            {
                "kind": "net",
                "name": name,
                "numBits": 1,
                "sigType": "signal",
                "is_global": True,
                "instance_terminal_count": 2,
            }
        )
        rows.append(
            {
                "kind": "terminal",
                "name": port,
                "direction": "inputOutput",
                "numBits": 1,
                "net": name,
            }
        )
    rows.append(
        {
            "kind": "net",
            "name": "unused",
            "numBits": 1,
            "sigType": "signal",
            "is_global": False,
            "instance_terminal_count": 0,
        }
    )
    return rows


def persisted_case(workspace, monkeypatch, change=None, project="a"):
    for legacy in ("CAD_CIRCUIT_TEMPLATES_DIR", "CAD_AI_TEMPLATE_ROOT"):
        monkeypatch.delenv(legacy, raising=False)
    root = workspace / ".cad/ai/reference/circuit-templates"
    root.mkdir(parents=True)
    rows = reference_rows()
    if change:
        change(rows)
    counts = Counter(r["kind"] for r in rows)
    rows.append(
        {
            "kind": "footer",
            "complete": True,
            "counts": {
                k: counts[k]
                for k in ("instance", "instance_terminal", "net", "terminal", "pin", "shape")
            },
        }
    )
    make_catalog(root, rows)
    catalog = TemplateCatalog(root)
    reference = catalog.query({"offset": 0, "limit": 1})["templates"][0]["template_ref"]
    record = catalog.get(reference)
    return selections(record, project), record


def selections(record, project="a"):
    terminal_names = {p: p if project == "a" else "pin_" + p.lower() for p in ("G", "D", "S", "B")}
    width, count = ("width", "fingers") if project == "a" else ("w_eff", "fold_count")
    masters, devices = [], []
    for index, source in enumerate(record["topology"]["devices"]):
        master_id = "chosen_" + str(index)
        masters.append(
            {
                "id": master_id,
                "kind": "device",
                "target": {
                    "library": "fixture_" + project,
                    "cell": "device_" + str(index),
                    "view": "symbol",
                },
                "library_path": "/fixture/" + project,
                "revision": "fixture-rev1",
                "terminals": [
                    {"name": terminal_names.get(p["name"], p["name"]), "direction": "inputOutput"}
                    for p in source["pins"]
                ],
                "terminals_complete": True,
                "parameters_complete": True,
                "parameters": [
                    {"name": width, "type": "string", "editable": True},
                    {"name": count, "type": "integer", "editable": True},
                ],
                "callbacks": {"status": "none"},
            }
        )
        devices.append(
            {
                "source_device": source["id"],
                "instance": "target_" + str(index),
                "master": master_id,
                "name": "M_" + str(index),
                "terminal_map": {
                    p["name"]: terminal_names.get(p["name"], p["name"]) for p in source["pins"]
                },
                "parameters": {width: "TARGET_WIDTH_" + project, count: index + 2},
            }
        )
    return {
        "template_ref": record["template_ref"],
        "target": {"library": "work_" + project, "cell": "adapted_inv", "view": "schematic"},
        "bindings": {
            "schema": "cad.circuit.bindings.v1",
            "project_ref": "fixture:" + project,
            "snapshot_ref": "snapshot:" + project,
            "source_ref": "synthetic-metadata-only",
            "evidence_kind": "test_fixture",
            "masters": masters,
        },
        "devices": devices,
        "net_map": {
            n["id"]: {"in": "A", "out": "Y"}[n["source_name"]]
            for n in record["topology"]["nets"]
            if n["source_name"] in {"in", "out"}
        },
        "port_map": {"VSS": "gnd!", "VDD": "vdd!"},
    }


def geometry_args(prepared):
    plan = prepared["plan"]
    masters = {m["id"]: m for m in plan["bindings"]["masters"]}
    anchors = {
        "G": ([-0.5, 0], "left"),
        "D": ([0, 0.5], "up"),
        "S": ([0, -0.5], "down"),
        "B": ([0.5, 0], "right"),
    }
    rows = [
        {
            "instance": i["id"],
            "master_revision": masters[i["master"]]["revision"],
            "parameters_digest": digest(i["parameters"]),
            "occupied_bbox": [[-0.5, -0.5], [0.5, 0.5]],
            "terminals": [
                {"name": name, "anchors": [{"id": "p0", "xy": xy, "escape": escape}]}
                for name, (xy, escape) in anchors.items()
            ],
        }
        for i in prepared["spec"]["instances"]
    ]
    return {
        "spec": prepared["spec"],
        "bindings": plan["bindings"],
        "template_use": prepared["template_use"],
        "preview_digest": prepared["preview_digest"],
        "geometry": {
            "schema": "cad.circuit.geometry.v1",
            "project_ref": prepared["spec"]["project_ref"],
            "binding_snapshot": prepared["spec"]["binding_snapshot"],
            "binding_digest": plan["binding_digest"],
            "source_ref": "synthetic-geometry-only",
            "evidence_kind": "test_fixture",
            "coordinate_system": "master_local_schematic_uu",
            "instances": rows,
        },
        "layout": {
            "instances": {
                "target_0": {"xy": [3, 3], "orientation": "R0"},
                "target_1": {"xy": [3, 23], "orientation": "MY"},
            },
            "ports": {
                "A": {"xy": [-3, 0], "escape": "right"},
                "Y": {"xy": [8, 10], "escape": "left"},
                "gnd!": {"xy": [-3, 18], "escape": "right"},
                "vdd!": {"xy": [-3, 20], "escape": "right"},
            },
            "anchor_choices": {},
            "grid": 0.125,
            "stub_length": 0.25,
            "clearance": 0.25,
            # Wiring is a user decision: the fixture records the confirmed stub
            # choice explicitly, like the other creation fixtures.
            "routing": {"mode": "stub", "decision": {"source": "user", "chosen": "stub"}},
        },
    }
