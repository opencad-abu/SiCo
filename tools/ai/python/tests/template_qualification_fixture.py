"""Complete saved path and symbol observations for standard qualification tests."""

from collections import Counter

from template_extract_fixture import CaptureClient, capture_rows, request

from cadai.template_capture import RECORD_FIELDS
from cadai.template_capture_geometry import RECORD_FIELDS_V2
from cadai.template_catalog import TemplateCatalog, call_template
from cadai.template_standard.reader import locate
from cadai.template_wire_contacts import NET_SEMANTICS


def wire(net, points):
    return dict(
        kind="shape", net=net, figure=dict(objType="line", lpp=["wire", "drawing"], points=points)
    )


def recount(rows):
    counts = Counter()
    for row in rows:
        if row["kind"] == "asset_header":
            counts.clear()
        elif row["kind"] == "asset_footer":
            row["counts"] = {k: counts[k] for k in RECORD_FIELDS | RECORD_FIELDS_V2}
        else:
            counts[row["kind"]] += 1
    return rows


def routed_rows():
    rows = capture_rows()
    rows[1]["wire_net_semantics"] = NET_SEMANTICS
    for row in rows:
        if row["kind"] == "port_anchor":
            row["xy"] = [-5, 5] if row["terminal"] == "A" else [5, 5]
    paths = []
    for net, sign in (("A", -1), ("Y", 1)):
        paths += [wire(net, [[sign, y], [3 * sign, y]]) for y in (0, 10)]
        paths += [
            wire(net, [[3 * sign, 0], [3 * sign, 10]]),
            wire(net, [[3 * sign, 5], [5 * sign, 5]]),
        ]
    return recount(rows[:-2] + paths + rows[-2:])


def symbol_rows(*, schematic=False):
    rows = routed_rows()
    header = dict(rows[1], asset="symbol", view="symbol")
    for key in ("electrical_observation", "wire_net_semantics"):
        header.pop(key)
    body = []
    for i, (name, direction) in enumerate((("A", "input"), ("Y", "output"))):
        body += [
            dict(kind="terminal", name=name, direction=direction, numBits=1, net=None),
            dict(
                kind="pin",
                terminal=name,
                net=None,
                figure=dict(objType="rect", bBox=[[i * 4 - 1, -1], [i * 4 + 1, 1]]),
            ),
        ]
    body += [
        dict(kind="shape", figure=dict(objType="polygon", points=[[0, -2], [4, 0], [0, 2]])),
        dict(
            kind="shape",
            figure=dict(
                objType="label",
                text='(progn "literal")',
                labelType="NLPLabel",
                xy=[2, 2],
                bBox=[[1, 1], [3, 3]],
            ),
        ),
        dict(kind="dependency_guard", before="symbol-fixture", after="symbol-fixture"),
    ]
    assets = ["schematic", "symbol"] if schematic else ["symbol"]
    prefix = rows[:-1] if schematic else [rows[0]]
    prefix[0]["assets"] = assets
    return recount(
        prefix
        + [
            header,
            *body,
            dict(kind="asset_footer", complete=True),
            dict(kind="bundle_footer", complete=True, asset_count=len(assets)),
        ]
    )


def collect_rows(tmp_path, rows, args=None):
    assets = rows[0]["assets"]
    args = args or (request() if "schematic" in assets else dict(library="demo", cell="INV"))
    args["assets"] = assets
    args["capture_schema"] = "cad.template.capture.v2"
    client = CaptureClient(tmp_path / "spool", recount(rows))
    result = call_template("extract_circuit_templates", args, workspace=tmp_path, client=client)
    item, _ = locate(TemplateCatalog(workspace=tmp_path), result["template_ref"])
    return result, item
