"""Synthetic saved capture transport for public extraction tests; no alternate builder."""

import hashlib
from collections import Counter
from types import SimpleNamespace

from test_circuit_templates import legacy_rows, write_capture

from cadai.template_capture import RECORD_FIELDS
from cadai.template_capture_geometry import RECORD_FIELDS_V2
from cadai.template_classification import SEMANTICS
from cadai.template_schema import CAPTURE_SCHEMA_V2


def evidence(kind):
    return dict(kind=kind, attributes={}, source_ref="fixture:reviewed-model", revision="1")


def capture_rows():
    legacy = legacy_rows()
    header = legacy[0] | dict(kind="asset_header", asset="schematic",
                             classification_semantics=SEMANTICS,
                             net_global_semantics="uniform_boolean_v1",
                             dynamic_text_policy="ignored_for_electrical_graph")
    header.pop("schema_version")
    header.pop("status")
    body = legacy[1:-1]
    for row in body:
        if row["kind"] == "net":
            row["name"] = {"in": "A", "out": "Y"}[row["name"]]
        if "net" in row:
            row["net"] = {"in": "A", "out": "Y"}[row["net"]]
    views = [dict(target=["demo", "INV", "schematic"], oa_view_type="schematic",
                  source_view="source", parameterized=False, hierarchy_count=0,
                  instance_count=2, mosaic_count=0, terminal_expressions=[], signal_expressions=[])]
    for row in body[:2]:
        row.update(terminal_names=["D", "G"], schematic_available=False,
                       master_oa_view_type="schematicSymbol", builtin_library=None)
        views.append(dict(views[0], target=[row[k] for k in ("libName", "cellName", "viewName")],
                          oa_view_type="schematicSymbol", source_view="master", instance_count=0))
        body.append(dict(kind="instance_geometry", instance=row["name"], library_path="/pdk",
                         observation=dict(ok=True, occupied_bbox=[[-1, -1], [1, 1]],
                                          pin_centers=[["D", [[1, 0]]], ["G", [[-1, 0]]]],
                                          signature="fixture-static-symbol")))
    header["electrical_observation"] = dict(schema="cad.template.electrical-observation.v1",
                                           views=views)
    body.extend([dict(kind="port_anchor", terminal=p, anchor_id="a0", xy=[i * 2, 0])
                 for i, p in enumerate(["A", "Y"])])
    body.append(dict(kind="dependency_guard", before="fixture", after="fixture"))
    return recount([dict(kind="bundle_header", schema_version=CAPTURE_SCHEMA_V2,
                         assets=["schematic"]), header, *body,
                    dict(kind="asset_footer", complete=True),
                    dict(kind="bundle_footer", complete=True, asset_count=1)])


def recount(rows):
    counts = Counter(r["kind"] for r in rows[2:-2])
    rows[-2]["counts"] = {k: counts[k] for k in (RECORD_FIELDS | RECORD_FIELDS_V2)}
    return rows


class CaptureClient:
    def __init__(self, path, rows):
        path.mkdir()
        self.runtime = SimpleNamespace(spool=path)
        self.path = write_capture(path, rows)
        self.calls = []

    def call(self, method, args):
        assert method == "capture_circuit_template"
        self.calls.append(args)
        raw = self.path.read_bytes()
        return True, dict(artifact=dict(path=str(self.path), size=len(raw),
                                       sha256=hashlib.sha256(raw).hexdigest()))


def request():
    return dict(library="demo", cell="INV", assets=["schematic"],
                classifications={"M0": evidence("nmos"), "M1": evidence("pmos")},
                reuse=dict(mode="whole", allow_rename=True))
