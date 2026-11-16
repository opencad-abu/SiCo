"""Bridge-backed lookups used by the design-target picker."""

from __future__ import annotations

import pytest
from sico.service.targets import BridgeTargetProvider, TargetBridgeError


class Broker:
    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []

    def circuit_call(self, context, params):
        self.calls.append((context, params))
        reply = self.replies.get(params["function"], {"circuit": {"ok": True}})
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_lookups_use_the_shared_bridge_contract():
    broker = Broker({
        "aiCopilotListLibraries": {"circuit": {"ok": True, "libraries": [
            {"name": "AnalogWork", "writable": True}]}},
        "aiCopilotListCells": {"circuit": {"ok": True, "cells": ["amp_tb"]}},
        "aiCopilotListViews": {"circuit": {"ok": True, "views": [
            {"view_name": "schematic", "oa_view_type": "schematic",
             "oa_view_type_source": "platform_api", "open": False}]}},
    })
    provider = BridgeTargetProvider(broker, "ctx")
    assert provider.libraries(include_readonly=False) == [{"name": "AnalogWork", "writable": True}]
    assert provider.cells("AnalogWork") == ["amp_tb"]
    assert provider.views("AnalogWork", "amp_tb") == [
        {"view_name": "schematic", "oa_view_type": "schematic",
         "oa_view_type_source": "platform_api", "open": False}]
    functions = [params["function"] for _context, params in broker.calls]
    assert functions == ["aiCopilotListLibraries", "aiCopilotListCells", "aiCopilotListViews"]
    assert broker.calls[0][1]["values"] == [False]
    assert broker.calls[1][1]["values"] == ["AnalogWork"]
    assert broker.calls[2][1]["values"] == ["AnalogWork", "amp_tb"]
    assert all(params["claim"] is None for _context, params in broker.calls)


def test_view_family_is_encoded_as_the_native_editor_family():
    broker = Broker({"aiCopilotListViews": {"circuit": {"ok": True, "views": []}}})
    provider = BridgeTargetProvider(broker, "ctx")
    provider.views("L", "C", ("maskLayout", "maskLayoutXL"))
    assert broker.calls[0][1]["values"] == ["L", "C", "layout"]


def test_view_rows_reject_unknown_oa_view_type_provenance():
    provider = BridgeTargetProvider(Broker({"aiCopilotListViews": {
        "circuit": {"ok": True, "views": [{
            "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "inferred_from_name",
            "open": False,
        }]}}}), "ctx")
    with pytest.raises(TargetBridgeError, match="invalid view type source"):
        provider.views("L", "C")


def test_open_target_asks_for_edit_mode_and_binds_the_window():
    broker = Broker({"aiCopilotOpenTarget": {"circuit": {
        "ok": True, "library": "AnalogWork", "cell": "amp_tb",
        "view_name": "schematic", "oa_view_type": "schematic",
        "oa_view_type_source": "platform_api", "window": "win:3",
        "window_ref": "wctx_fixture_1"}}})
    provider = BridgeTargetProvider(broker, "ctx")
    target = {"library": "AnalogWork", "cell": "amp_tb",
              "view_name": "schematic", "oa_view_type": "schematic",
              "oa_view_type_source": "platform_api"}
    data = provider.open_target(target)
    assert broker.calls[0][1]["values"] == [
        "AnalogWork", "amp_tb", "schematic", "schematic", "a", "platform_api",
    ]
    assert data["window"] == "win:3"
    assert data["window_ref"] == "wctx_fixture_1"
    assert "window" not in target
    broker2 = Broker({"aiCopilotOpenTarget": {"circuit": {
        "ok": True, "window": "win:4", "oa_view_type_source": "platform_api",
    }}})
    BridgeTargetProvider(broker2, "ctx").open_target({**target, "read_only": True})
    assert broker2.calls[0][1]["values"][-2:] == ["r", "platform_api"]


def test_bridge_refusals_and_missing_replies_read_as_errors():
    refusal = {"circuit": {"ok": False, "code": "circuit_requires_target",
                           "message": "没有捕获目标"}}
    provider = BridgeTargetProvider(Broker({"aiCopilotListCells": refusal}), "ctx")
    with pytest.raises(TargetBridgeError, match="没有捕获目标"):
        provider.cells("AnalogWork")
    with pytest.raises(TargetBridgeError):
        BridgeTargetProvider(Broker({"aiCopilotListCells": {"unexpected": True}}), "ctx").cells("L")
    with pytest.raises(TargetBridgeError):
        BridgeTargetProvider(None, "ctx").libraries()


@pytest.mark.parametrize("rows", [None, {}, ["bad row"], [{"name": "missing type"}]])
def test_malformed_view_rows_are_rejected_before_presentation(rows):
    provider = BridgeTargetProvider(Broker({"aiCopilotListViews": {
        "circuit": {"ok": True, "views": rows}}}), "ctx")
    with pytest.raises(TargetBridgeError, match="invalid target rows"):
        provider.views("L", "C")
