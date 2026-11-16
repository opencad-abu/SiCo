"""Ground terminals must have physical basic/gnd links; implicit globals are opt-in."""

import copy

import pytest
from circuit_geometry_fixture import sample_geometry

from cadai.circuit_create import payload
from cadai.circuit_geometry import preview_geometry
from cadai.circuit_spec_plan import preview_circuit
from cadai.circuit_spec_schema import CircuitSpecError, digest


def ground_args():
    args = sample_geometry()
    spec, bindings, geometry, layout = (args[k] for k in ("spec", "bindings", "geometry", "layout"))
    spec.pop("global_net_authorization")
    spec["nets"][-1] = dict(name="GND", scope="ground")
    master = dict(
        id="ground",
        target=dict(library="basic", cell="gnd", view="symbol"),
        library_path="/cds/basic",
        revision="ground1",
        kind="device",
        terminals=[dict(name="gnd!", direction="inputOutput")],
        terminals_complete=True,
        parameters=[],
        parameters_complete=True,
        callbacks=dict(status="none"),
    )
    bindings["masters"].append(master)
    layout["pin_links"] = []
    for inst in list(spec["instances"]):
        terminal = next(t for t, net in inst["connections"].items() if net == "gnd!")
        inst["connections"][terminal] = "GND"
        ground = "ground_" + inst["id"]
        spec["instances"].append(
            dict(
                id=ground,
                name="G_" + inst["id"],
                master="ground",
                role="auxiliary",
                parameters={},
                connections={"gnd!": "GND"},
                unconnected={},
            )
        )
        x = layout["instances"][inst["id"]]["xy"][0]
        layout["instances"][ground] = dict(xy=[x, -1], orientation="R0")
        layout["pin_links"].append(
            [dict(instance=inst["id"], terminal=terminal), dict(instance=ground, terminal="gnd!")]
        )
        geometry["instances"].append(
            dict(
                instance=ground,
                master_revision="ground1",
                parameters_digest=digest({}),
                occupied_bbox=[[-0.0625, -0.25], [0.0625, 0]],
                terminals=[dict(name="gnd!", anchors=[dict(id="pin0", xy=[0, 0], escape="up")])],
            )
        )
    refresh(args)
    return args


def refresh(args):
    pre = preview_circuit(args["spec"], args["bindings"])
    args["preview_digest"] = pre["preview_digest"]
    args["geometry"]["binding_digest"] = pre["plan"]["binding_digest"]


def test_ground_symbols_are_physically_linked_without_global_labels():
    args = ground_args()
    before = copy.deepcopy(args)
    plan = preview_geometry(**args)
    assert plan["plan"]["geometry_checks_passed"], plan
    wires = [w for w in plan["plan"]["wires"] if w["net"] == "GND"]
    assert len(wires) == 3 and all(w["label"] is None and w["end_endpoint"] for w in wires)
    native = payload(dict(**args, geometry_plan_digest=plan["geometry_plan_digest"]))
    assert len([w for w in native[3] if w[0] is None]) == 3
    assert all(v != "GND" for row in native[1] for _, v in row[6])
    assert args == before


def test_every_missing_ground_link_is_reported_in_one_reply():
    """缺失的接地链必须一次列全：逐条报错要多付两轮预览。"""
    args = ground_args()
    args["layout"]["pin_links"] = args["layout"]["pin_links"][:-2]
    with pytest.raises(CircuitSpecError) as failure:
        preview_geometry(**args)
    message = str(failure.value)
    # 每条缺链留下两个未覆盖端点（原实例端子 + 地符号端子），一次全部列出。
    assert "4 problem(s)" in message
    assert message.count(
        "every ground endpoint requires a physical basic/gnd pin link"
    ) == 4
    assert '"instance":"stim","terminal":"N"' in message.replace(" ", "")


@pytest.mark.parametrize(
    "change",
    [
        "missing_link",
        "duplicate",
        "wrong_net",
        "not_collinear",
        "wrong_escape",
        "fake_ground",
        "two_grounds",
    ],
)
def test_invalid_physical_grounding_rejected(change):
    args = ground_args()
    layout = args["layout"]
    if change == "missing_link":
        layout["pin_links"].pop()
    elif change == "duplicate":
        layout["pin_links"].append(layout["pin_links"][0])
    elif change == "wrong_net":
        layout["pin_links"][0][0]["terminal"] = "P"
    elif change == "not_collinear":
        layout["instances"]["ground_load"]["xy"][0] += 0.5
    elif change == "wrong_escape":
        layout["instances"]["ground_load"]["orientation"] = "R180"
    elif change == "fake_ground":
        args["bindings"]["masters"][-1]["target"]["library"] = "fakeBasic"
    elif change == "two_grounds":
        layout["pin_links"][0][0] = dict(instance="ground_stim", terminal="gnd!")
    refresh(args)
    with pytest.raises(CircuitSpecError):
        preview_geometry(**args)


def test_global_tb_requires_explicit_user_request_record():
    args = sample_geometry()
    args["spec"].pop("global_net_authorization")
    with pytest.raises(CircuitSpecError, match="explicit user authorization"):
        refresh(args)
    args["spec"]["global_net_authorization"] = (
        "User explicitly requested global supplies for this test"
    )
    refresh(args)
    assert preview_geometry(**args)["plan"]["geometry_checks_passed"]
