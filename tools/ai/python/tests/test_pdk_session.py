import json
from copy import deepcopy

import pytest

from cadai.pdk_normalize import condition, context, detail, digest
from cadai.pdk_schema import PDK_TOOLS, PdkArgumentError, PdkUnavailable, arguments, skill_string
from cadai.pdk_session import PdkSession
from pdk_fixture import Bridge, fixture, raw


def selected(service):
    search = service.call("search_pdk_devices", {"library": "synthetic"})
    args = {"snapshot_ref": search["context"]["snapshot_ref"], "device_ref": search["items"][0]["device_ref"]}
    return args, service.call("get_pdk_device", args)


@pytest.mark.parametrize("value,status,expected", [
    (None, "unknown", None), ("", "context_required", None), ("t", "known", True),
    ("nil", "known", False), ("cdfgData->x", "context_required", None),
    (0, "context_required", None), ("true", "context_required", None),
])
def test_conditions_preserve_literal_vs_expression(value, status, expected):
    c = condition(raw(value))
    assert (c["status"], c["value"]) == (status, expected)
    assert c["raw"]["value"] == value


def test_unknown_classification_bus_order_callbacks_and_default_are_preserved():
    ctx, data = fixture()
    value = detail(data, context(ctx))
    assert value["classification"]["kind"] == "unknown"
    p = value["parameters"]["items"][0]
    assert p["cdf_type"] == "string" and p["raw"]["type"]["value"] == "cellData"
    assert p["editable"]["value"] is None and p["current"]["status"] == "not_applicable"
    assert p["default"]["value"] == "" and p["units"]["physical_unit"] is None
    assert value["ports"]["items"][0]["bus"]["members"] == ["DATA<3>", "DATA<2>", "DATA<1>", "DATA<0>"]
    assert value["callbacks"]["items"][0]["presence"] == "present"


def test_search_pagination_is_frozen_and_query_bound():
    bridge = Bridge()
    service = PdkSession(bridge=bridge)
    args = {"library": "synthetic", "page_size": 1}
    first = service.call("search_pdk_devices", args)
    ref = first["context"]["snapshot_ref"]
    more = {**args, "snapshot_ref": ref, "cursor": first["page"]["next_cursor"]}
    bridge.count = 0
    second = service.call("search_pdk_devices", more)
    assert second["items"][0]["target"]["cell"] == "cell1"
    assert len(bridge.calls) == 1
    with pytest.raises(PdkUnavailable, match="identical"):
        service.call("search_pdk_devices", {**more, "page_size": 2})
    other = service.call("search_pdk_devices", args)
    with pytest.raises(PdkUnavailable, match="Cursor"):
        service.call("search_pdk_devices", {**more, "snapshot_ref": other["context"]["snapshot_ref"]})


def test_detail_pagination_uses_frozen_revision_and_unique_sections():
    bridge = Bridge()
    bridge.data["cdf"]["parameters"]["items"] *= 30
    service = PdkSession(bridge=bridge)
    args, first = selected(service)
    bridge.data["cdf"]["parameters"]["items"] = []
    more = service.call("get_pdk_device", {**args, "cursor": first["page"]["next_cursor"]})
    assert more["device"]["revision"] == first["device"]["revision"]
    assert len(bridge.calls) == 2
    with pytest.raises(PdkUnavailable, match="Cursor"):
        service.call("get_pdk_device", {**args, "sections": ["ports"], "cursor": first["page"]["next_cursor"]})


def test_detail_pages_do_not_refresh_the_live_directory():
    bridge = Bridge()
    bridge.data["cdf"]["parameters"]["items"] *= 30
    service = PdkSession(bridge=bridge)
    search = service.call("search_pdk_devices", {"library": "synthetic"})
    args = {"snapshot_ref": search["context"]["snapshot_ref"],
            "device_ref": search["items"][0]["device_ref"], "page_size": 1}
    service._legacy_direct = False

    def unexpected(_args):
        raise AssertionError("a frozen detail page refreshed the live directory")

    service.preparation.directory = unexpected
    first = service.call("get_pdk_device", args)
    assert first["page"]["next_cursor"]
    second = service.call(
        "get_pdk_device", {**args, "cursor": first["page"]["next_cursor"]}
    )
    assert second["device"]["revision"] == first["device"]["revision"]


def test_standard_snapshot_skips_the_legacy_selection_gate():
    service = PdkSession(bridge=Bridge())
    service._legacy_direct = False

    def unexpected(_library):
        raise AssertionError("published standard snapshot re-ran the legacy selection gate")

    service.preparation.current_selection = unexpected
    snapshot = {
        "query": {"library": "synthetic", "view": "symbol"},
        "directory_library": "synthetic",
        "directory_source": "published_standard",
    }
    assert service._snapshot_selection(snapshot) is None


@pytest.mark.parametrize("change,expected", [
    ("path", "library_binding"), ("generation", "session_generation"),
    ("session", "session_ref"), ("cdf", "cdf"), ("geometry", "database"),
])
def test_live_revalidation_detects_changes_without_replacing_old_binding(change, expected):
    bridge = Bridge()
    service = PdkSession(bridge=bridge)
    args, first = selected(service)
    check = {"snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]}
    assert service.call("revalidate_pdk_bindings", check)["status"] == "valid"
    if change == "path":
        bridge.ctx["libraries"][0]["resolved_path"] = "/different/path"
    elif change == "generation":
        bridge.ctx["bridge_generation"] += 1
    elif change == "session":
        bridge.ctx["session_ref"] = "session:new"
    elif change == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["defValue"] = raw("new")
    else:
        bridge.data["database"]["geometry"]["bbox"] = raw(None)
    response = service.call("revalidate_pdk_bindings", check)
    assert response["status"] == "changed" and expected in response["items"][0]["changes"]
    assert response["cache_used_for_observation"] is False
    assert service.call("get_pdk_device", args)["device"]["revision"] == first["device"]["revision"]


@pytest.mark.parametrize("part", ["parameters", "simulators", "ports", "geometry"])
def test_unavailable_metadata_never_validates(part):
    bridge = Bridge()
    parent = "cdf" if part in {"parameters", "simulators"} else "database"
    bridge.data[parent][part]["status"] = "unavailable"
    service = PdkSession(bridge=bridge)
    args, _ = selected(service)
    v = service.call("revalidate_pdk_bindings", {"snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]})
    assert v["status"] == "unavailable" and part in v["items"][0]["unverified"]


def test_nil_empty_missing_and_opaque_metadata_are_distinct():
    assert len({digest(raw(None)), digest(raw("")), digest(raw(0)), digest({})}) == 4
    ctx, data = fixture()
    data["cdf"]["parameters"]["items"][0]["defValue"] = {"status": "unsupported", "type": "opaque", "value": None}
    assert detail(data, context(ctx))["parameters"]["status"] == "partial"
    assert condition(None)["status"] == "unknown"


def test_missing_library_before_detail_requires_new_search():
    bridge = Bridge()
    service = PdkSession(bridge=bridge)
    search = service.call("search_pdk_devices", {})
    bridge.ctx["libraries"] = []
    with pytest.raises(PdkUnavailable, match="library_unavailable"):
        service.call("get_pdk_device", {"snapshot_ref": search["context"]["snapshot_ref"], "device_ref": search["items"][0]["device_ref"]})


@pytest.mark.parametrize("args", [{"page_size": True}, {"page_size": 0}, {"callback": "run"},
                                  {"library": "a\nb"}, {"cursor": "x"}, {"view": None}])
def test_invalid_arguments_fail_before_bridge(args):
    with pytest.raises(PdkArgumentError):
        arguments("search_pdk_devices", args)


def test_skill_quote_and_read_only_mcp_permissions():
    from cadai.mcp import McpServer
    assert skill_string('a" ) error("bad")') == json.dumps('a" ) error("bad")')
    server = McpServer(object())
    listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {tool["name"] for tool in listed["result"]["tools"]}
    for tool in PDK_TOOLS:
        assert tool["annotations"]["readOnlyHint"] is (tool["name"] not in {"init_pdk", "resolve_pdk_baseline", "prepare_pdk_cdf_validation", "advance_pdk_cdf_validation", "cancel_pdk_cdf_validation", "finalize_pdk_cdf_validation", "collect_pdk_parameter_facts", "import_pdk_facts", "collect_pdk_data", "prepare_pdk_data_update", "apply_pdk_data_update", "prepare_pdk_baseline_update", "apply_pdk_baseline_update", "publish_pdk_data", "prepare_pdk_parameter_draft", "submit_pdk_parameter_batch", "cancel_pdk_parameter_draft"})
        assert not tool["annotations"]["destructiveHint"]
        assert tool["name"] in names


def test_optional_verification_profile_rejects_pdk_tools():
    from inspect import signature
    from cadai.mcp import McpServer
    if "profile" not in signature(McpServer).parameters:
        pytest.skip("verification profile belongs to the separate Agent integration")
    server = McpServer(object(), profile="verification")
    for tool in PDK_TOOLS:
        assert "error" in server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool["name"], "arguments": {}}})


def test_detail_response_limit_and_no_items_lost():
    bridge = Bridge()
    params = []
    for i in range(35):
        p = deepcopy(bridge.data["cdf"]["parameters"]["items"][0])
        p["name"] = raw(str(i))
        p["description"] = raw("large" * 1000)
        params.append(p)
    bridge.data["cdf"]["parameters"]["items"] = params
    service = PdkSession(bridge=bridge)
    search = service.call("search_pdk_devices", {})
    args = {"snapshot_ref": search["context"]["snapshot_ref"], "device_ref": search["items"][0]["device_ref"],
            "sections": ["parameters"], "page_size": 100}
    names = []
    while True:
        page = service.call("get_pdk_device", args)
        assert len(json.dumps(page, ensure_ascii=False, indent=2).encode()) <= 90000
        names.extend(p["name"] for p in page["device"]["parameters"]["items"])
        if page["page"]["next_cursor"] is None:
            break
        args["cursor"] = page["page"]["next_cursor"]
    assert names == list(map(str, range(35)))


def test_oversize_item_and_cache_eviction_are_explicit():
    service = PdkSession(bridge=Bridge())
    first = service.call("search_pdk_devices", {})
    for _ in range(8):
        service.call("search_pdk_devices", {})
    with pytest.raises(PdkUnavailable, match="evicted"):
        service.call("search_pdk_devices", {"snapshot_ref": first["context"]["snapshot_ref"]})
    response = {"items": []}
    with pytest.raises(PdkUnavailable, match="Single item"):
        service.store.page(response, ["x" * 100000], "ref", {}, 0, 20, assign=lambda r, i: r.update(items=i))


def test_absent_cdf_and_missing_conditions_do_not_become_default_values():
    ctx, data = fixture()
    data["cdf"].update(presence="absent", cell_metadata={})
    data["cdf"]["parameters"]["items"] = []
    value = detail(data, context(ctx))
    assert value["parameters"]["presence"] == "absent"
    assert value["callbacks"]["status"] == "complete" and value["callbacks"]["count"] == 0
    ctx, data = fixture()
    data["cdf"]["parameters"]["items"][0].pop("editable")
    assert detail(data, context(ctx))["parameters"]["items"][0]["editable"]["status"] == "unknown"


def test_technology_path_change_invalidates_selected_library():
    bridge = Bridge()
    tech = deepcopy(bridge.ctx["libraries"][0])
    tech.update(name="tech", resolved_path="/tech/one")
    bridge.ctx["libraries"].append(tech)
    service = PdkSession(bridge=bridge)
    args, _ = selected(service)
    tech["resolved_path"] = "/tech/two"
    result = service.call("revalidate_pdk_bindings", {"snapshot_ref": args["snapshot_ref"], "device_refs": [args["device_ref"]]})
    assert result["status"] == "changed" and "library_binding" in result["items"][0]["changes"]


def test_multiple_pin_figures_keep_candidates_and_port_identity():
    ctx, data = fixture()
    box = {"status": "known", "type": "list", "value": [
        {"status": "known", "type": "list", "value": [raw(0), raw(0)]},
        {"status": "known", "type": "list", "value": [raw(2), raw(2)]}]}
    data["database"]["geometry"]["items"] = [
        {"terminal": "DATA<3:0>", "pin_index": pin, "figure_index": fig, "status": "complete",
         "raw": {"bBox": box, "objType": raw("rect")}} for pin, fig in [(0, 0), (0, 1), (1, 0)]]
    value = detail(data, context(ctx))
    figures = value["geometry"]["items"]
    assert len({f["figure_ref"] for f in figures}) == 3
    assert {f["port_ref"] for f in figures} == {value["ports"]["items"][0]["port_ref"]}
    assert all(f["anchors"]["selected"] is None for f in figures)
    assert figures[0]["anchors"]["candidates"][0]["point"] == [1, 1]


def test_button_and_incomplete_bus_remain_explicit():
    ctx, data = fixture()
    data["cdf"]["parameters"]["items"][0]["paramType"] = raw("button")
    data["database"]["ports"]["items"][0]["members_status"] = "unavailable"
    value = detail(data, context(ctx))
    assert value["parameters"]["items"][0]["editable"]["status"] == "not_applicable"
    assert value["parameters"]["items"][0]["parameter_kind"] == "action"
    assert value["ports"]["status"] == "partial"


@pytest.mark.parametrize("val,typ,expected", [(None, "nil", False), ("t", "symbol", True)])
def test_boolean_default_json_value_has_explicit_cdf_type_and_raw_value(val, typ, expected):
    ctx, data = fixture()
    param = data["cdf"]["parameters"]["items"][0]
    param.update(paramType=raw("boolean"), defValue=raw(val, typ))
    normalized = detail(data, context(ctx))["parameters"]["items"][0]
    assert normalized["value_type"] == "boolean" and normalized["default"]["value"] is expected
    assert normalized["default"]["raw"] == raw(val, typ)


def test_classification_search_and_unknown_shape_report_partial():
    response = PdkSession(bridge=Bridge()).call("search_pdk_devices", {"family": "mos"})
    assert response["items"] == [] and response["status"] == "partial"
    assert response["unavailable_matches"] == ["classification_provider_unavailable"]
    ctx, data = fixture()
    data["database"]["geometry"]["items"] = [{"terminal": "DATA<3:0>", "pin_index": 0,
        "figure_index": 0, "status": "complete", "raw": {"objType": raw("unsupportedFutureShape")}}]
    assert detail(data, context(ctx))["geometry"]["status"] == "partial"
