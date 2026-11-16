"""Completion preserves decisions, checks sources, and never authorizes uncertain geometry."""

import pytest
from pdk_fixture import Bridge
from pdk_standard_fixture import package, replace, update

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_normalize import context, detail
from cadai.pdk_session import PdkSession
from cadai.pdk_standard import geometry
from cadai.pdk_standard.jsonio import LIMIT
from cadai.pdk_standard.source_check import observed as check
from cadai.pdk_standard.symbol_facts import derive
from cadai.pdk_standard.workspace import select


def raw(value):
    return {
        "status": "known",
        "type": "list"
        if isinstance(value, list)
        else "string"
        if isinstance(value, str)
        else "float",
        "value": [raw(x) for x in value] if isinstance(value, list) else value,
    }


def shape(points):
    return {
        "objType": raw("line"),
        "points": raw(points),
        "bBox": raw(
            [
                [min(p[0] for p in points), min(p[1] for p in points)],
                [max(p[0] for p in points), max(p[1] for p in points)],
            ]
        ),
    }


class GeometryBridge(Bridge):
    def __init__(self):
        super().__init__()
        database = self.data["database"]
        database["ports"]["items"][0].update(name="P", width=1, pin_count=1, members=["P"])
        database["geometry"].update(
            bbox=raw([[0, 0], [1, 1]]),
            parameterized_master=False,
            hierarchical_instance_count=0,
            body_outline_status="not_requested",
            items=[
                {
                    "terminal": "P",
                    "pin_index": 0,
                    "figure_index": 0,
                    "status": "complete",
                    "raw": {"objType": raw("rect"), "bBox": raw([[0.95, 0.95], [1.05, 1.05]])},
                }
            ],
        )


def legacy(tmp_path, monkeypatch):
    import pdk_standard_fixture

    original = geometry.signature
    with monkeypatch.context() as patch:
        patch.setattr(pdk_standard_fixture, "Bridge", GeometryBridge)
        patch.setattr(geometry, "METHOD", "sico-symbol-interface-json-v1")
        patch.setattr(
            geometry,
            "signature",
            lambda v, method=None: original(v, "sico-symbol-interface-json-v1"),
        )
        current, bridge, value, _ = package(tmp_path)
    bridge.data["database"]["geometry"].update(
        mosaic_count=0,
        body_outline_status="complete",
        body_shapes=[shape([[0, 0], [1, 0], [1, 1]])],
    )
    return current, bridge, value


def test_completion_preserves_rules_is_bounded_and_reuses_attempt(tmp_path, monkeypatch):
    current, bridge, value = legacy(tmp_path, monkeypatch)
    updates, preview = update(tmp_path, current, [replace("/items/cell0/use/circuit", "allow")])
    updates.apply({"update_ref": preview["update_ref"]})
    current = select(tmp_path, "synthetic", {})
    before = current.document("cell0/cdf.json")
    devices = current.document("device.json")
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"})
    result = session.call(
        "collect_pdk_data",
        {"library": "synthetic", "device": "cell0", "revision": current.revision},
    )
    assert result["collected"] and result["resolved_fields"] == 2
    assert result["status"] == "incomplete" and result["remaining_count"] > 0
    completed = select(tmp_path, "synthetic", {})
    assert completed.document("cell0/cdf.json") == before
    assert completed.document("device.json") == devices
    symbol = completed.document("cell0/symbol.json")
    assert symbol["bbox"] == [[0, 0], [1, 1]]
    assert symbol["terminals"]["P"]["anchors"] == [{"id": "pin0", "xy": [1, 1], "escape": "up"}]
    assert symbol["grid"]["state"] == "unknown"
    count = len(bridge.calls)
    again = session.call(
        "collect_pdk_data",
        {"library": "synthetic", "device": "cell0", "revision": result["revision"]},
    )
    assert (
        not again["collected"]
        and again["revision"] == result["revision"]
        and len(bridge.calls) == count
    )
    assert max(p.stat().st_size for p in completed.overlay.rglob("*.json")) <= LIMIT
    # Later host updates preserve the additive geometry dependency and derived evidence.
    updates, preview = update(tmp_path, completed, [replace("/items/cell0/use/testbench", "allow")])
    updates.apply({"update_ref": preview["update_ref"]})
    latest = select(tmp_path, "synthetic", {})
    assert latest.document("cell0/symbol.json") == symbol
    assert latest.dependency_updates == completed.dependency_updates
    captured = bridge.capture("device", value["target"])
    live = detail(captured["data"], context(captured["context"]))
    check(latest, "cell0", live, symbol["depends_on"])
    live["geometry"]["body_shapes"][0]["points"] = raw([[0, 0], [1, 1]])
    with pytest.raises(PdkUnavailable, match="Source changed"):
        check(latest, "cell0", live, symbol["depends_on"])


@pytest.mark.parametrize("failure", ["revision", "cdf", "dirty", "concurrent", "write"])
def test_failure_does_not_publish(tmp_path, monkeypatch, failure):
    current, bridge, _ = legacy(tmp_path, monkeypatch)
    index = tmp_path / ".sico/ai/pdk-data/index.json"
    before = index.read_bytes()
    args = {"library": "synthetic", "device": "cell0", "revision": current.revision}
    if failure == "revision":
        args["revision"] = "stale"
    if failure == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["defValue"] = raw("999")
    if failure == "dirty":
        bridge.data["database"]["master_state"]["modified"] = True
    if failure == "concurrent":
        from cadai.pdk_standard import completion

        real = completion.select
        calls = []

        def select_changed(*a):
            result = real(*a)
            calls.append(1)
            if len(calls) > 1:
                result.revision = "changed"
            return result

        monkeypatch.setattr(completion, "select", select_changed)
    if failure == "write":
        from cadai.pdk_standard import overlay

        real = overlay.atomic

        def broken(path, value):
            if path.name == "index.json":
                raise OSError("write failed")
            return real(path, value)

        monkeypatch.setattr(overlay, "atomic", broken)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"})
    with pytest.raises((PdkUnavailable, OSError)):
        session.call("collect_pdk_data", args)
    assert index.read_bytes() == before


def test_no_guess_for_corner_hierarchy_or_nonrectangular_pins(tmp_path, monkeypatch):
    _, bridge, value = legacy(tmp_path, monkeypatch)
    captured = bridge.capture("device", value["target"])
    live = detail(captured["data"], context(captured["context"]))
    live["geometry"]["body_shapes"] = [shape([[0, 0], [1, 1]])]
    assert derive(live)["terminals"]["P"]["state"] == "unknown"
    live["geometry"]["mosaic_count"] = 1
    assert derive(live)["bbox"]["state"] == "unknown"
    live["geometry"]["mosaic_count"] = 0
    live["geometry"]["items"][0]["raw"]["objType"] = raw("polygon")
    assert derive(live)["bbox"]["state"] == "unknown"


def test_v3_fingerprint_excludes_relocation_handles_but_includes_body(tmp_path, monkeypatch):
    _, bridge, value = legacy(tmp_path, monkeypatch)
    capture = bridge.capture("device", value["target"])
    before = detail(capture["data"], context(capture["context"]))
    capture["context"]["libraries"][0]["resolved_path"] = "/moved/pdk"
    capture["data"]["identity"]["resolved_view_path"] = "/moved/pdk/cell0/symbol"
    after = detail(capture["data"], context(capture["context"]))
    assert geometry.signature(before) == geometry.signature(after)
    assert geometry.signature(before, "sico-symbol-interface-json-v1") == geometry.signature(
        after, "sico-symbol-interface-json-v1"
    )
    after["geometry"]["body_shapes"][0]["points"] = raw([[0, 0], [1, 1]])
    assert geometry.signature(before) != geometry.signature(after)
    with pytest.raises(PdkUnavailable, match="Unsupported"):
        geometry.signature(before, "future-method")


def test_rectangular_polygon_proof_rejects_crossing_edges():
    from cadai.pdk_standard.symbol_facts import _rectangular

    box = [[-1, -1], [1, 1]]
    polygon = {"objType": raw("polygon"), "points": raw([[-1, -1], [1, -1], [1, 1], [-1, 1]])}
    assert _rectangular(polygon, box)
    polygon["points"] = raw([[-1, -1], [1, 1], [1, -1], [-1, 1]])
    assert not _rectangular(polygon, box)


def test_new_full_capture_uses_same_geometry_producer(tmp_path, monkeypatch):
    import pdk_standard_fixture

    class FullBridge(GeometryBridge):
        def __init__(self):
            super().__init__()
            self.data["database"]["geometry"].update(
                mosaic_count=0,
                body_outline_status="complete",
                body_shapes=[shape([[0, 0], [1, 0], [1, 1]])],
            )

    monkeypatch.setattr(pdk_standard_fixture, "Bridge", FullBridge)
    current, _, _, _ = package(tmp_path)
    symbol = current.document("cell0/symbol.json")
    assert symbol["bbox"] == [[0, 0], [1, 1]]
    assert symbol["terminals"]["P"]["anchors"][0]["escape"] == "up"
    source = symbol["evidence"]["/bbox"]
    assert current.document("sources.json")["items"][source]["kind"] == "derived"


def test_legacy_v2_uses_its_declared_projection(tmp_path, monkeypatch):
    current, bridge, old = legacy(tmp_path, monkeypatch)
    old["geometry"]["body_outline_status"] = "not_requested"
    capture = bridge.capture("device", old["target"])
    live = detail(capture["data"], context(capture["context"]))
    method = "sico-symbol-interface-json-v2"
    assert geometry.signature(old, method) == geometry.signature(live, method)


def test_cdf_modes_is_available_through_standard_query(tmp_path):
    current, bridge, _, _ = package(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"})
    calls = len(bridge.calls)
    result = session.call(
        "get_pdk_data", {"library": "synthetic", "device": "cell0", "section": "cdf_modes"}
    )
    assert result["status"] == "incomplete" and result["items"][0]["unresolved"] == ["w"]
    assert len(bridge.calls) == calls


def test_ruled_netlist_override_requires_its_scope_record(tmp_path):
    current, bridge, value, _ = package(tmp_path)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"})
    base = {"library": "synthetic", "device": "cell0", "revision": current.revision}
    with pytest.raises(PdkUnavailable, match="together"):
        session.call("collect_pdk_data", {**base, "netlist_name": "nt25_ckt"})
    with pytest.raises(PdkUnavailable, match="scope record"):
        session.call("collect_pdk_data", {**base, "netlist_name": "nt25_ckt",
                                          "identity_evidence": str(tmp_path / "absent.json")})


def test_ruled_netlist_override_records_the_ruling_source(tmp_path, monkeypatch):
    import hashlib

    import cadai.pdk_standard.simulation_collection as collection

    current, bridge, value = legacy(tmp_path, monkeypatch)
    decision = tmp_path / "decision.json"
    decision.write_text('{"ruling": "nt25_ckt"}')
    before_cdf = current.document("cell0/cdf.json")
    seen = []

    def fake_build(package, row, value, source, derived, net_name=None):
        seen.append(net_name)
        dependency = package.manifest["dependencies"][row["dir"] + "_interface"]
        terminals = sorted(package.document(row["dir"] + "/symbol.json")["terminals"])
        order = ["p%d" % index for index in range(len(terminals))]
        interface = {"simulator": "spectre", "kind": "subckt", "name": net_name,
                     "model_source": "builtin",
                     "term_order": order,
                     "terminal_map": dict(zip(terminals, order)),
                     "parameters": {}}
        document = {"source": source, "depends_on": [row["dir"] + "_interface"],
                    "interfaces": {"spectre": interface},
                    "evidence": {"/interfaces": derived}}
        return document, {row["dir"] + "_interface": dependency}, None

    monkeypatch.setattr(collection, "build", fake_build)
    session = PdkSession(bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"})
    result = session.call("collect_pdk_data", {
        "library": "synthetic", "device": "cell0", "revision": current.revision,
        "netlist_name": "nt25_ckt", "identity_evidence": str(decision)})
    assert result["collected"] and seen == ["nt25_ckt"]
    done = select(tmp_path, "synthetic", {})
    sources = done.document("sources.json")["items"]
    ruling_id = next(key for key, row in sources.items() if row.get("kind") == "user")
    ruling = sources[ruling_id]
    assert ruling["scope_ref"] == str(decision)
    assert ruling["scope_digest"] == "sha256:" + hashlib.sha256(decision.read_bytes()).hexdigest()
    assert "nt25_ckt" in ruling["summary"]
    simulation = done.document("cell0/simulation.json")
    assert simulation["evidence"]["/interfaces/spectre/name"] == ruling_id
    derived_id = next(key for key, row in sources.items() if row.get("kind") == "derived")
    assert ruling_id in sources[derived_id]["inputs"]
    assert done.document("cell0/cdf.json") == before_cdf


def test_build_uses_the_ruled_netlist_name_override(tmp_path, monkeypatch):
    import cadai.pdk_standard.simulation_collection as collection
    from cadai.pdk_standard.jsonio import fail as json_fail

    current, bridge, value, _ = package(tmp_path)
    seen = {}

    def fake_resolve(name, simulator, rows):
        seen[simulator] = name
        return ["tt"], ["d", "g", "s", "b"], "subckt"

    captured = {}

    def fake_project(items, name, refs, decks, terminals=None, defaults=None):
        captured["name"] = name
        return {"spectre": {"name": name}}, []

    monkeypatch.setattr(collection, "entries", lambda package, simulator: [("tt", None, None, None)])
    monkeypatch.setattr(collection, "resolve", fake_resolve)
    monkeypatch.setattr(collection, "project", fake_project)
    row = current.document("device.json")["items"]["cell0"]
    document, dependency, note = collection.build(
        current, row, value, "s", "d", net_name="nt25_ckt")
    assert note is None and seen == {"hspiceD": "nt25_ckt", "spectre": "nt25_ckt"}
    assert captured["name"] == "nt25_ckt" and document["interfaces"]["spectre"]["name"] == "nt25_ckt"
