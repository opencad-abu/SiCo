from __future__ import annotations

from pathlib import Path


from aivworkbench.pvt_adapter import (
    PhysicalPVTLauncher,
    PhysicalPVTLauncherRegistry,
    build_physical_pvt_plan,
    empty_physical_pvt_registry,
)
from aivworkbench.pvt_contract import (
    PVT_BLOCKED_ENVIRONMENT,
    PVT_READY,
    PVT_STALE_ARTIFACT,
)
from pvt_adapter_fixtures import (_contract, _cases, _setup, _registry, _file_registry, _fsdb_contract)


def test_default_registry_is_empty_and_unregistered_is_blocked(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(),
        registry=empty_physical_pvt_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_BLOCKED_ENVIRONMENT
    assert plan.summary["code"] == "pvt_adapter_unregistered"
    assert plan.summary["physical_pvt_execution"] == "not_invoked"
    assert not (payload_root / "spectre").exists()


def test_registered_plan_is_controlled_but_does_not_create_outputs(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_READY
    assert len(plan.points) == 4
    assert plan.summary["adapter_registration"] == "registered"
    assert plan.summary["model_section_verification"]["status"] == "PASS"
    assert plan.summary["physical_pvt_execution"] == "not_invoked"
    assert plan.summary["design_verdict"] == "NOT_ESTABLISHED"
    assert all(point.command[0] == tools["spectre"] for point in plan.points)
    assert all("-section" in point.command for point in plan.points)
    assert plan.locators == ()
    assert all(
        locator["exists"] is False
        for locator in plan.summary["expected_raw_outputs"]
    )
    assert not (payload_root / "spectre").exists()


def test_fsdb_plan_declares_file_locator_kind(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _fsdb_contract(),
        registry=_file_registry(),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_READY
    assert all(item["kind"] == "file" for item in plan.summary["expected_raw_outputs"])
    assert all(point.output_path.name == "fsdb" for point in plan.points)


def test_missing_launcher_tool_is_blocked_before_builder(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, _tools = _setup(tmp_path)
    called = []

    def builder(_context):
        called.append(True)
        return ()

    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(builder),
        tools={},
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_BLOCKED_ENVIRONMENT
    assert plan.summary["code"] == "pvt_launcher_tools_unavailable"
    assert called == []


def test_launcher_declaration_requires_all_registered_tools(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    registry = PhysicalPVTLauncherRegistry()
    registry.register(
        PhysicalPVTLauncher(
            name="spectre_pvt_extra_tool_v1",
            version="1",
            simulator="spectre",
            raw_output="psf",
            required_tools=("spectre", "deckgen"),
            command_tool="spectre",
            command_builder=lambda _context: (),
        )
    )
    value = _contract()
    value["execution"] = {
        **value["execution"],
        "launcher": "spectre_pvt_extra_tool_v1",
    }
    result = build_physical_pvt_plan(
        value,
        registry=registry,
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert result.status == PVT_BLOCKED_ENVIRONMENT
    assert result.summary["code"] == "pvt_launcher_tools_unavailable"
    assert result.summary["missing_tools"] == ["deckgen"]


def test_stale_output_is_detected_before_command_builder(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    stale = payload_root / "spectre" / "pvt" / "pvt-0000" / "psf"
    stale.mkdir(parents=True)
    called = []

    def builder(context):
        called.append(context.point["id"])
        return _registry().require("spectre_pvt_test_v1").command_builder(context)

    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(builder),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_STALE_ARTIFACT
    assert plan.summary["code"] == "pvt_output_stale"
    assert called == []


def test_plan_to_dict_is_json_safe_and_exposes_no_publishable_locator(tmp_path: Path) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(), registry=_registry(), tools=tools, model_root=model_root,
        payload_root=payload_root, deck_root=deck_root, cases=_cases()
    )
    encoded = plan.to_dict(payload_root, deck_root=deck_root)
    import json

    json.dumps(encoded, allow_nan=False)
    assert encoded["locators"] == []
    assert encoded["summary"]["physical_pvt_execution"] == "not_invoked"
    assert encoded["points"][0]["deck_relative"] == "pvt-0000.deck"
