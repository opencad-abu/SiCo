"""Synthetic adapter data. No project defaults are supplied by production modules."""

from copy import deepcopy

from pdk_fixture import Bridge, raw

from cadai.pdk_binding import PdkBindings
from cadai.pdk_session import PdkSession


def tagged(value):
    if isinstance(value, list):
        return dict(status="known", type="list", value=[tagged(v) for v in value])
    return raw(value)


def fixture():
    bridge = Bridge()
    bridge.count = 1
    data = bridge.data
    p = data["cdf"]["parameters"]["items"][0]
    p.update(editable=raw(None), callback=raw(None), choices=raw(None))
    db = data["database"]
    port = db["ports"]["items"][0]
    port.update(name="A", width=1, members=["A"], net_name="A", pin_count=1)
    db["geometry"].update(
        coordinate_system="master_local_schematic_user_units",
        bbox=tagged([[-1, -1], [1, 1]]),
        parameterized_master=False,
        hierarchical_instance_count=0,
        items=[
            dict(
                terminal="A",
                pin_index=0,
                figure_index=0,
                status="complete",
                raw=dict(bBox=tagged([[-0.125, -0.125], [0.125, 0.125]]), objType=raw("rect")),
            )
        ],
    )
    service = PdkSession(bridge=bridge)
    found = service.call("search_pdk_devices", {"library": "synthetic"})
    selection = dict(
        snapshot_ref=found["context"]["snapshot_ref"], device_ref=found["items"][0]["device_ref"]
    )
    detail = service.call("get_pdk_device", selection)["device"]
    args = dict(
        **selection,
        revision=detail["revision"],
        master_id="device",
        kind="device",
        project_ref="project:test",
        project_policy_ref="test:policy",
        writable_parameters=[
            dict(name="w", policy="absent_condition", evidence="explicit test policy")
        ],
        pin_escapes=[dict(figure_ref=detail["geometry"]["items"][0]["figure_ref"], escape="left")],
    )
    return (
        bridge,
        PdkBindings(
            service,
            body_reader=lambda d: dict(
                occupied_bbox=[[-1, -1], [1, 1]], pin_centers=[["A", [[0, 0]]]], signature="test"
            ),
        ),
        args,
    )


def prepare_args(record):
    from cadai.circuit_spec_schema import digest

    return dict(
        bindings=dict(
            masters=[deepcopy(record["master"])],
            evidence_kind="session_capture",
            project_ref=record["project_ref"],
        ),
        spec=dict(instances=[dict(id="M1", master="device", parameters={"w": "1u"})]),
        geometry=dict(
            instances=[
                dict(record["geometry"], instance="M1", parameters_digest=digest({"w": "1u"}))
            ]
        ),
    )
