"""Contract and stale-dependency coverage for formal PDK consumption."""

from copy import deepcopy

import pytest
from pdk_binding_fixture import fixture, prepare_args, tagged

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import McpServer
from cadai.pdk_binding_data import geometry_rows, parameter_rows, read_device


def test_bind_preserves_sources_explicit_policy_and_generic_inputs():
    bridge, adapter, args = fixture()
    before = deepcopy(bridge.data)
    record = adapter.bind(args)
    assert record["master"]["parameters"] == [dict(name="w", type="string", editable=True)]
    assert record["master"]["pdk_binding"]["revision"] == args["revision"]
    assert record["geometry"]["terminals"][0]["anchors"] == [
        dict(id="pin0", xy=[0, 0], escape="left")
    ]
    assert record["validation"][0]["status"] == "valid"
    assert bridge.data == before
    adapter.validate_prepare(prepare_args(record))
    assert len([c for c in bridge.calls if c[0] == "device"]) == 3


def test_bind_records_the_confirmed_netlist_terminal_map():
    _, adapter, args = fixture()
    assert adapter.bind(args)["master"]["pdk_binding"]["netlist_terminal_map_status"] == "unverified"
    device, _ = read_device(adapter.session, args)
    ports = [port["name"] for port in device["ports"]["items"]]
    mapping = {ports[0]: "d"}
    record = adapter.bind({**args, "netlist_terminal_map": mapping})
    binding = record["master"]["pdk_binding"]
    assert binding["netlist_terminal_map"] == mapping
    assert binding["netlist_terminal_map_status"] == "confirmed"
    assert [row["name"] for row in record["master"]["terminals"]] == ports
    # A live fixture capture carries no collected readiness; prepared databases do.
    assert "device_readiness" in record and record["device_readiness"] is None


def test_explicit_project_classification_survives_resolution_and_is_digest_bound():
    _, adapter, args = fixture()
    old = adapter.bind(args)
    evidence = dict(kind="nmos", attributes={}, source_ref="project:reviewed-devices",
                    revision="rev1")
    bound = adapter.bind({**args, "classification": evidence})
    assert bound["binding_ref"] != old["binding_ref"]
    prepared = prepare_args(bound)
    resolved = adapter.resolve_request(prepared)
    assert resolved["bindings"]["masters"][0]["classification"] == evidence
    adapter.validate_prepare(resolved)
    changed = adapter.bind({**args, "classification": {**evidence, "revision": "rev2"}})
    assert changed["binding_ref"] != bound["binding_ref"]
    prepared["bindings"]["masters"][0]["classification"]["kind"] = "pmos"
    with pytest.raises(CircuitSpecError, match="altered after binding"):
        adapter.validate_prepare(prepared)
    with pytest.raises(CircuitSpecError, match="classification is unknown"):
        adapter.bind({**args, "classification": {**evidence, "kind": "unknown"}})


def test_annotation_proxy_is_retained_and_live_changes_invalidate_the_binding():
    _, adapter, args = fixture()
    original = adapter.body_reader
    proxy = [[-2, -2], [2, 2]]
    adapter.body_reader = lambda device: {**original(device), "annotation_bbox": deepcopy(proxy)}
    bound = adapter.bind(args)
    assert bound["geometry"]["annotation_bbox"] == proxy
    adapter.validate_prepare(prepare_args(bound))
    proxy[1][0] += 1
    with pytest.raises(CircuitSpecError, match="body changed since binding"):
        adapter.validate_execute([bound["binding_ref"]])


def test_project_classification_cannot_override_collected_kind_or_attributes(monkeypatch):
    from cadai import pdk_binding

    _, adapter, args = fixture()
    original = pdk_binding.read_device

    def read(session, request):
        device, ctx = original(session, request)
        device["classification"] = dict(kind="nmos", attributes={"flavor": "standard"})
        return device, ctx

    monkeypatch.setattr(pdk_binding, "read_device", read)
    evidence = dict(kind="nmos", attributes={"flavor": "standard"},
                    source_ref="project:reviewed-devices", revision="rev1")
    bound = adapter.bind({**args, "classification": evidence})
    assert bound["master"]["classification"] == evidence
    for changed in ({**evidence, "kind": "pmos"}, {**evidence, "attributes": {}}):
        with pytest.raises(CircuitSpecError, match="differs from collected evidence"):
            adapter.bind({**args, "classification": changed})


@pytest.mark.parametrize("change", ["generation", "library", "cdf", "geometry"])
def test_live_change_rejected_at_bind_prepare_and_execute(change):
    bridge, adapter, args = fixture()
    record = adapter.bind(args)
    if change == "generation":
        bridge.ctx["bridge_generation"] += 1
    elif change == "library":
        bridge.ctx["libraries"][0]["resolved_path"] += "-changed"
    elif change == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["defValue"] = tagged("changed")
    else:
        bridge.data["database"]["geometry"]["bbox"] = tagged([[-2, -2], [2, 2]])
    for call in (
        lambda: adapter.bind(args),
        lambda: adapter.validate_prepare(prepare_args(record)),
        lambda: adapter.validate_execute([record["source_ref"]]),
    ):
        with pytest.raises(CircuitSpecError, match="live dependencies changed"):
            call()


@pytest.mark.parametrize(
    "field", ["revision", "library_path", "callbacks", "pdk_binding", "project_ref", "geometry"]
)
def test_creation_rejects_altered_adapter_components(field):
    _, adapter, args = fixture()
    prepared = prepare_args(adapter.bind(args))
    if field == "project_ref":
        prepared["bindings"][field] = "another"
    elif field == "geometry":
        prepared["geometry"]["instances"][0]["occupied_bbox"][0][0] = -2
    else:
        prepared["bindings"]["masters"][0][field] = (
            "tampered" if field != "pdk_binding" else {"adapter_ref": "pdk-adapter:unknown"}
        )
    with pytest.raises(CircuitSpecError):
        adapter.validate_prepare(prepared)


def test_evicted_producer_snapshot_keeps_live_validation_for_bound_device():
    _, adapter, args = fixture()
    result = adapter.bind(args)
    for _ in range(8):
        adapter.session.call("search_pdk_devices", {})
    checks = adapter.validate_execute([result["source_ref"]])
    assert checks[0]["status"] == "valid"
    assert checks[0]["cache_used_for_observation"] is False


@pytest.mark.parametrize(
    "condition,policy,extension,valid",
    [
        (None, "absent_condition", None, True),
        (None, "static_true", None, False),
        ("nil", "absent_condition", None, False),
        ("nil", "static_true", None, False),
        ("t", "static_true", None, True),
        ("expr()", "static_true", None, False),
        ("expr()", "project_extension", None, False),
        ("expr()", "project_extension", "project:registered", True),
    ],
)
def test_editable_unknown_and_dynamic_require_matching_project_policy(
    condition, policy, extension, valid
):
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    from cadai.pdk_normalize import condition as normalize

    device["parameters"]["items"][0]["editable"] = normalize(tagged(condition))
    args["writable_parameters"][0]["policy"] = policy
    if extension:
        args["extension_ref"] = extension
    if valid:
        assert parameter_rows(device, args)[0][0]["editable"]
    else:
        with pytest.raises(CircuitSpecError):
            parameter_rows(device, args)


def test_default_readonly_buttons_and_callbacks_are_not_inferred():
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    args["writable_parameters"] = []
    assert parameter_rows(device, args)[0][0]["editable"] is False
    device["parameters"]["items"][0]["cdf_type"] = "button"
    assert parameter_rows(device, args)[2] == ["w"]
    device["callbacks"]["items"][0]["presence"] = "present"
    with pytest.raises(CircuitSpecError, match="installed callback support"):
        parameter_rows(device, args)


@pytest.mark.parametrize(
    "mutation",
    ["multi_figure", "path", "triangle", "bus", "inherit", "pcell", "hierarchy", "escapes"],
)
def test_unsupported_geometry_is_not_silently_approximated(mutation):
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    geo, port = device["geometry"], device["ports"]["items"][0]
    fig = geo["items"][0]
    if mutation == "multi_figure":
        fig["figure_index"] = 1
    elif mutation == "path":
        fig["shape_type"] = "path"
    elif mutation == "triangle":
        fig.update(shape_type="polygon")
        fig["raw"]["points"] = tagged([[0, 0], [1, 0], [0, 1]])
    elif mutation == "bus":
        port["width"] = 4
    elif mutation == "inherit":
        port["net_expression"] = tagged("[@x]")
    elif mutation == "pcell":
        geo["parameterized_master"] = True
    elif mutation == "hierarchy":
        geo["hierarchical_instance_count"] = 1
    else:
        args["pin_escapes"] = []
    with pytest.raises(CircuitSpecError):
        geometry_rows(device, args)


def test_adapter_reads_complete_frozen_detail_without_mcp_pages():
    _, adapter, args = fixture()
    original_call = adapter.session.call

    def no_detail_pages(name, query):
        assert name != "get_pdk_device"
        return original_call(name, query)

    adapter.session.call = no_detail_pages
    assert adapter.bind(args)["ok"]
    args["revision"] = "different"
    with pytest.raises(CircuitSpecError, match="revision differs"):
        adapter.bind(args)


def test_incomplete_complete_detail_is_rejected():
    _, adapter, args = fixture()
    original = adapter.session.complete_device

    def incomplete(snapshot_ref, device_ref):
        device, context = original(snapshot_ref, device_ref)
        device["parameters"]["items"].pop()
        return device, context

    adapter.session.complete_device = incomplete
    with pytest.raises(CircuitSpecError, match="parameters detail incomplete"):
        adapter.bind(args)


def test_pin_escape_error_distinguishes_figure_and_port_refs():
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    args["pin_escapes"] = [
        dict(figure_ref=device["ports"]["items"][0]["port_ref"], escape="left")
    ]
    with pytest.raises(CircuitSpecError, match="figure_ref.*not.*port_ref"):
        geometry_rows(device, args)


def test_standard_symbol_escapes_fill_an_empty_pin_escape_list():
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    args["pin_escapes"] = []
    rule = {"symbol": {"terminals": {
        "A": {"anchors": [{"id": "pin0", "xy": [0, 0], "escape": "left"}]}}}}
    terms = geometry_rows(device, args, rule)[2]
    assert terms[0]["anchors"] == [{"id": "pin0", "xy": [0, 0], "escape": "left"}]


def test_mcp_registration_and_bind_route():
    _, adapter, args = fixture()
    server = McpServer(object())
    server._pdk_session = adapter.session
    server._pdk_bindings = adapter
    response = server.handle(
        dict(
            jsonrpc="2.0",
            id=1,
            method="tools/call",
            params=dict(name="bind_pdk_device", arguments=args),
        )
    )
    assert not response["result"]["isError"]
    assert server._pdk_bindings.records


def test_removed_source_and_changed_creator_body_rejected():
    _, adapter, args = fixture()
    record = adapter.bind(args)
    prepared = prepare_args(record)
    del prepared["bindings"]["masters"][0]["pdk_binding"]
    with pytest.raises(CircuitSpecError, match="provenance was removed"):
        adapter.validate_prepare(prepared)
    old = adapter.body_reader
    adapter.body_reader = lambda d: dict(old(d), signature="changed")
    with pytest.raises(CircuitSpecError, match="body changed"):
        adapter.validate_execute([record["source_ref"]])


def test_unwritable_cyclic_whitespace_choices_remain_in_source():
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    parameter = device["parameters"]["items"][0]
    parameter.update(cdf_type="cyclic", choices=tagged([" ", "yes", "no"]))
    args["writable_parameters"] = []
    row = parameter_rows(device, args)[0][0]
    assert row == dict(name="w", type="string", editable=False)
    assert parameter["choices"] == tagged([" ", "yes", "no"])


def test_body_revision_creates_new_immutable_adapter_ref():
    _, adapter, args = fixture()
    first = adapter.bind(args)
    reader = adapter.body_reader
    adapter.body_reader = lambda d: dict(reader(d), signature="different-body")
    second = adapter.bind(args)
    assert first["source_ref"] != second["source_ref"]
    assert adapter.get(first["source_ref"])["body_observation"] == first["body_observation"]
    with pytest.raises(CircuitSpecError, match="body changed"):
        adapter.validate_execute([first["source_ref"]])


def test_multi_pin_anchors_preserved_and_rectangle_polygon_accepted():
    _, adapter, args = fixture()
    device, _ = read_device(adapter.session, args)
    figure = device["geometry"]["items"][0]
    figure.update(shape_type="polygon")
    figure["raw"]["points"] = tagged(
        [[-0.125, -0.125], [0.125, -0.125], [0.125, 0.125], [-0.125, 0.125]]
    )
    duplicate = deepcopy(figure)
    duplicate.update(figure_ref="figure:second", pin_index=1)
    device["geometry"]["items"].append(duplicate)
    device["ports"]["items"][0]["pin_count"] = 2
    args["pin_escapes"].append(dict(figure_ref="figure:second", escape="right"))
    terms = geometry_rows(device, args)[2]
    assert terms[0]["anchors"] == [
        dict(id="pin0", xy=[0, 0], escape="left"),
        dict(id="pin1", xy=[0, 0], escape="right"),
    ]
