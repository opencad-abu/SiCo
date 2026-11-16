from __future__ import annotations

from pathlib import Path

import pytest

from aivworkbench.pvt_adapter import (
    PhysicalPVTLauncher,
    PhysicalPVTLauncherRegistry,
    PVTAdapterError,
    build_physical_pvt_plan,
    verify_approved_model_sections,
)
from aivworkbench.pvt_contract import (
    PVT_BLOCKED_INPUT,
)
from pvt_adapter_fixtures import (_contract, _cases, _setup, _registry)


def test_registry_rejects_duplicate_or_incomplete_launcher() -> None:
    registry = PhysicalPVTLauncherRegistry()
    launcher = PhysicalPVTLauncher(
        name="spectre_pvt_test_v1", version="1", simulator="spectre",
        raw_output="psf", required_tools=("spectre",), command_tool="spectre",
        command_builder=lambda _context: (),
    )
    registry.register(launcher)
    with pytest.raises(PVTAdapterError, match="duplicate"):
        registry.register(launcher)
    with pytest.raises(PVTAdapterError, match="omit simulator prerequisites"):
        registry.register(
            PhysicalPVTLauncher(
                name="ams_bad", version="1", simulator="ams", raw_output="psf",
                required_tools=("runams",), command_tool="runams",
                command_builder=lambda _context: (),
            )
        )


@pytest.mark.parametrize(
    "field, value, message",
    (
        ("name", "bad name", "name is invalid"),
        ("version", "1 beta", "version must be non-empty"),
        ("required_tools", "spectre", "required_tools must be an array"),
    ),
)
def test_registry_rejects_malformed_launcher_fields(
    field: str, value: object, message: str
) -> None:
    values = {
        "name": "valid_launcher",
        "version": "1",
        "simulator": "spectre",
        "raw_output": "psf",
        "required_tools": ("spectre",),
        "command_tool": "spectre",
        "command_builder": lambda _context: (),
    }
    values[field] = value
    with pytest.raises(PVTAdapterError, match=message):
        PhysicalPVTLauncherRegistry().register(PhysicalPVTLauncher(**values))


@pytest.mark.parametrize(
    "builder, code",
    (
        (lambda context: (context.tools["spectre"], str(context.deck_path), "/tmp/outside", context.model_section), "pvt_command_unqualified"),
        (lambda context: (context.tools["spectre"], str(context.model_path), context.model_section, str(context.deck_path), "-raw", str(context.output_path), "bad\narg"), "pvt_command_unqualified"),
        (lambda context: (_ for _ in ()).throw(RuntimeError("builder boom")), "pvt_command_builder_failed"),
    ),
)
def test_command_builder_is_fail_closed(tmp_path: Path, builder, code: str) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(builder),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_BLOCKED_INPUT
    assert plan.summary["code"] == code
    assert not (payload_root / "spectre").exists()


def test_command_root_resolution_race_is_blocked_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root, payload_root, deck_root, tools = _setup(tmp_path)
    original_resolve = Path.resolve
    armed = False

    def flaky_resolve(path, *args, **kwargs):
        if armed and path == payload_root:
            raise RuntimeError("payload root symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)

    def builder(context):
        # Arm a simulated filesystem race after context construction.  The
        # adapter must return a blocked plan, not leak RuntimeError.
        nonlocal armed
        armed = True
        return (
            context.tools["spectre"],
            "-section",
            context.model_section,
            "-model",
            str(context.model_path),
            str(context.deck_path),
            "-raw",
            str(context.output_path),
        )

    plan = build_physical_pvt_plan(
        _contract(),
        registry=_registry(builder),
        tools=tools,
        model_root=model_root,
        payload_root=payload_root,
        deck_root=deck_root,
        cases=_cases(),
    )
    assert plan.status == PVT_BLOCKED_INPUT
    assert plan.summary["code"] == "pvt_command_unqualified"
    assert plan.summary["physical_pvt_execution"] == "not_invoked"


def test_model_section_verifier_rejects_missing_and_duplicate_sections(tmp_path: Path) -> None:
    model = tmp_path / "device.scs"
    model.write_text("section tt {\n}\nsection tt {\n}\n", encoding="utf-8")
    result = verify_approved_model_sections(model, {"tt": "tt", "ss": "ss"})
    assert result["status"] == "FAIL"
    assert result["missing"] == ["ss"]
    assert result["duplicates"] == ["tt"]
    invalid = verify_approved_model_sections(model, {"tt": "bad section"})
    assert invalid["status"] == "BLOCKED_INPUT"
