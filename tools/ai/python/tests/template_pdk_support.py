"""Owned live PDK session and physical readback for P1 reference acceptance."""

import hashlib
import json
import math
from contextlib import contextmanager

from circuit_create_support import AI_ROOT, session
from test_circuit_rc_gui import mcp_call

from cadai.circuit_create import skill_literal
from cadai.pdk_binding import PdkBindings
from cadai.pdk_bridge import PdkBridge
from cadai.pdk_files import parse_si
from cadai.pdk_session import PdkSession
from cadai.template_catalog import TemplateCatalog


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


@contextmanager
def pdk_session(path, pdk, source_lib=None, *, library="gpdk045"):
    with session(path) as (bridge, server, _):
        with (path / "cds.lib").open("a") as stream:
            stream.write("DEFINE " + library + " " + str(pdk) + "\n")
            if source_lib:
                stream.write("DEFINE amsLDO " + str(source_lib) + "\n")
        bridge.skill("ddUpdateLibList()")
        for name in (
            "AI_pdk_init.il",
            "AI_pdk_core.il",
            "AI_pdk_cdf.il",
            "AI_pdk_db.il",
            "AI_pdk_capture.il",
            "AI_cdf_data.il",
            "AI_cdf_probe.il",
            "AI_cdf_callbacks.il",
            "AI_templates.il",
            "AI_template_capture_geometry.il",
        ):
            bridge.skill("load(" + json.dumps(str(AI_ROOT / "skill" / name)) + ")")
        # Exact-cell live reads in an isolated test; no workspace-wide PDK collection.
        service = PdkSession(bridge=PdkBridge(server.client))
        server._pdk_session = service
        server._pdk_bindings = PdkBindings(service, server.client)
        server.client.runtime = server.client.dispatcher.runtime
        original = bridge.request

        def request(message, timeout):
            if message["method"] == "capture_circuit_template":
                bridge.evaluate_path(
                    message["source_path"], message["output_path"], evaluator="aiEvalExpression"
                )
                return True, {"result_path": message["output_path"]}
            return original(message, timeout)

        bridge.request = request
        yield bridge, server


def bind_device(server, cell, names, escapes, *, classification=None, library="gpdk045"):
    service = server._pdk_session
    found = service.call("search_pdk_devices", dict(library=library, cell=cell))
    assert len(found["items"]) == 1, found
    query = dict(
        snapshot_ref=found["context"]["snapshot_ref"],
        device_ref=found["items"][0]["device_ref"],
        tier="all",
        sections=["geometry"],
        page_size=100,
    )
    figures = []
    while True:
        page = service.call("get_pdk_device", query)
        figures.extend(page["device"]["geometry"]["items"])
        if page["page"]["next_cursor"] is None:
            break
        query["cursor"] = page["page"]["next_cursor"]
    return server._pdk_bindings.bind(
        dict(
            **({"classification": classification} if classification is not None else {}),
            snapshot_ref=query["snapshot_ref"],
            device_ref=query["device_ref"],
            revision=page["device"]["revision"],
            master_id=cell,
            kind="device",
            project_ref="project:p1-live",
            project_policy_ref="test:explicit-p1-parameters",
            writable_parameters=[
                dict(name=n, policy="cdf_callback", evidence="P1 sizing") for n in names
            ],
            callback_order=names,
            pin_escapes=[
                dict(figure_ref=f["figure_ref"], escape=escapes[f["terminal"]]) for f in figures
            ],
        )
    )


def capture(server, path, cell, library="genericWork"):
    result = mcp_call(
        server,
        "extract_circuit_templates",
        dict(
            library=library,
            cell=cell,
            assets=["schematic"],
            capture_schema="cad.template.capture.v2",
        ),
    )
    assert result["ok"], result
    record = TemplateCatalog(workspace=path).get(result["template_ref"])
    save_json(path / (cell + "-record.json"), record)
    return record


def oa_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create(server, args, key):
    task = mcp_call(
        server,
        "begin_circuit_task",
        dict(task_id=key, presentation="background", choice_confirmed=True),
    )
    prepared = mcp_call(
        server, "prepare_circuit_creation", dict(args, task_ref=task["task_ref"], request_id=key)
    )
    assert prepared["ok"], prepared
    execute = dict(
        prepare_ref=prepared["prepare_ref"],
        request_id=key,
        geometry_plan_digest=args["geometry_plan_digest"],
    )
    done = mcp_call(server, "create_circuit_from_plan", execute)
    assert done["ok"] and done["saved"] and done["readback_verified"], done
    assert done["check"] == [0, 0], done
    assert mcp_call(server, "create_circuit_from_plan", execute) == done
    inspected = mcp_call(server, "inspect_created_circuit", dict(circuit_ref=done["circuit_ref"]))
    assert inspected["ok"] and inspected["readback_verified"], inspected
    return dict(created=done, inspected=inspected, replay_equal=True)


def readback(bridge, spec, wires):
    """Independently compare drawn grid-edge sets and effective requested CDF values."""
    target = spec["target"]
    requested = [[i["name"], list(i["parameters"])] for i in spec["instances"]]
    code = (
        "let((prCv prResult prCdf) prCv=dbOpenCellViewByType("
        + " ".join(json.dumps(target[k]) for k in ("library", "cell", "view"))
        + ' "" "r") prResult=aiCircuitJson(list('
        'foreach(mapcar prShape setof(prS prCv~>shapes car(prS~>lpp)=="wire" '
        'prS~>objType=="line") list(prShape~>net~>name prShape~>points)) '
        "foreach(mapcar prRow "
        + skill_literal(requested)
        + " prCdf=cdfGetInstCDF(dbFindAnyInstByName(prCv car(prRow))) "
        "list(car(prRow) foreach(mapcar prName cadr(prRow) "
        "list(prName cdfFindParamByName(prCdf prName)~>value)))))) "
        "dbClose(prCv) prResult)"
    )
    rows = json.loads(bridge.skill(code))
    actual = _edge_set(rows[0])
    expected = _edge_set([[w["net"], w["points"]] for w in wires])
    assert actual == expected, dict(
        missing=sorted(expected - actual), extra=sorted(actual - expected)
    )
    params = {name: dict(values or []) for name, values in rows[1]}
    for inst in spec["instances"]:
        actual_params = params[inst["name"]]
        assert set(actual_params) == set(inst["parameters"])
        for name, wanted in inst["parameters"].items():
            if actual_params[name] != wanted:
                # PDK callbacks may normalize 32u to 32.0u. Compare literals as
                # quantities; mode selectors and nonnumeric strings stay exact.
                a, b = parse_si(actual_params[name]), parse_si(wanted)
                assert a is not None and b is not None and math.isclose(
                    a, b, rel_tol=1e-9, abs_tol=1e-24
                ), (inst["name"], name, wanted, actual_params[name])
    return dict(wire_grid_edges=len(actual), wire_geometry_equal=True, parameters=params)


def _edge_set(rows):
    edges = set()
    for net, points in rows:
        for a, b in zip(points, points[1:]):
            a, b = [tuple(round(v / 0.0625) for v in p) for p in (a, b)]
            assert a[0] == b[0] or a[1] == b[1]
            axis = 0 if a[1] == b[1] else 1
            low, high = sorted((a, b))
            for value in range(low[axis], high[axis]):
                start, end = list(low), list(low)
                start[axis], end[axis] = value, value + 1
                edges.add((net, tuple(start), tuple(end)))
    return edges
