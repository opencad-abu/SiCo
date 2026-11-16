"""Contracts identified by the 2026-09-17 session failure audit."""

from copy import deepcopy

import pytest
from circuit_geometry_fixture import sample_geometry
from pdk_binding_fixture import fixture
from simulation_recipe_fixture import recipe

from cadai.circuit_create import CREATE_TOOLS
from cadai.circuit_generic import GENERIC_TOOLS
from cadai.circuit_spec_schema import CircuitSpecError, validate
from cadai.device_catalog import DeviceCatalogUnavailable, query_catalog
from cadai.pdk_binding_data import read_device
from cadai.simulation_recipe import native_recipe, preview_recipe
from cadai.simulation_schema import PATH
from sico.adapters.artifacts import register_artifacts
from sico.adapters.circuit import CircuitClient, PreflightRequired
from sico.core.contracts import BoundContext, ToolCall
from sico.core.tools import Tool, ToolRegistry
from sico.storage.journal import SessionJournal


@pytest.mark.parametrize(
    "name", ["preview_circuit_spec", "preview_circuit_geometry", "prepare_circuit_creation"]
)
@pytest.mark.parametrize(
    "source,valid",
    [
        ((), False),
        (("binding_refs",), True),
        (("bindings",), False),
        (("geometry",), False),
        (("bindings", "geometry"), True),
        (("binding_refs", "bindings"), False),
        (("binding_refs", "geometry"), False),
        (("binding_refs", "bindings", "geometry"), False),
    ],
)
def test_binding_source_schema_matches_runtime_choices(name, source, valid):
    full = sample_geometry()
    full.update(
        binding_refs=["pdk-binding:fixture"],
        geometry_plan_digest="a" * 64,
        task_ref="task:fixture",
        request_id="fixture",
    )
    schema = next(t["inputSchema"] for t in GENERIC_TOOLS + CREATE_TOOLS if t["name"] == name)
    args = {k: full[k] for k in schema["required"]}
    args.update({k: full[k] for k in source})
    if name == "preview_circuit_spec":
        valid = set(source) in ({"binding_refs"}, {"bindings"})
    before = deepcopy(schema)
    if valid:
        validate(args, schema)
    else:
        with pytest.raises(CircuitSpecError):
            validate(args, schema)
    assert schema == before


@pytest.mark.parametrize(
    "value,schema,message",
    [
        ("spectre", PATH, "absolute path"),
        (65, {"type": "integer", "minimum": 1, "maximum": 64}, "1..64"),
        pytest.param(
            10**5000, {"type": "integer", "minimum": 1, "maximum": 64}, "1..64", id="huge-integer"
        ),
        ([1, 2], {"type": "array", "minItems": 0, "maxItems": 1}, "0..1"),
    ],
)
def test_bounds_and_path_errors_are_actionable(value, schema, message):
    with pytest.raises(CircuitSpecError, match=message):
        validate(value, schema)


def test_artifact_errors_preserve_the_reason_and_checksums(tmp_path):
    with SessionJournal(tmp_path, "artifact-review") as journal:
        registry = ToolRegistry()
        register_artifacts(registry, journal)
        manifest = journal.artifact(b'{"valid":true}')
        args = {k: manifest[k] for k in ("path", "sha256")}
        context = BoundContext("i", "g", "target", {})
        for changes, text in [
            ({"offset": -1}, ">= 0"),
            ({"path": "/tmp/measurements.json"}, "query_maestro_results"),
            ({"sha256": "0" * 64}, "checksum"),
        ]:
            result = registry.execute(
                ToolCall("bad", "read_artifact", {**args, **changes}), context
            )
            assert result.status == "invalid_arguments", result
            assert text in result.summary.lower(), result
        result = registry.execute(ToolCall("good", "read_artifact", args), context)
        assert result.status == "ok" and result.data["text"] == '{"valid":true}'
        large = journal.artifact(b"x" * 5000)
        result = registry.execute(
            ToolCall("large", "read_artifact", {
                **{k: large[k] for k in ("path", "sha256")}, "limit": 20000,
            }), context
        )
        assert result.status == "ok"
        assert len(result.data["text"]) == 4000
        assert result.data["next_offset"] == 4000


def test_revision_mismatch_points_to_device_detail_without_relaxing_equality():
    _, adapter, args = fixture()
    args["revision"] = "summary-revision"
    with pytest.raises(CircuitSpecError, match="get_pdk_device.device.revision") as error:
        read_device(adapter.session, args)
    assert "summary-revision" in str(error.value) and "summary_revision" in str(error.value)


def test_missing_catalog_suggests_prepared_pdk_search(tmp_path):
    with pytest.raises(DeviceCatalogUnavailable, match="search_pdk_devices"):
        query_catalog({}, database=str(tmp_path / "absent.sqlite3"))


def test_preflight_reports_all_missing_requirements_before_any_write(tmp_path, monkeypatch):
    context = BoundContext("i", "g", "target", {"cwd": str(tmp_path)})
    expected = [
        "task:review",
        "foreground",
        ["Work", "Tb", "maestro"],
        "maestro",
        None,
        str(tmp_path / "Work"),
        str(tmp_path / "runs"),
    ]
    calls = []
    client = CircuitClient(None, context, tmp_path)
    client.requirements = dict(
        presentation="background", targets=[], libraries=[], directories=[], model_files=[]
    )

    def send(function, values):
        calls.append(function)
        assert function == "aiCopilotDescribe"
        return dict(ok=True, binding=expected, model_files=["/models/test.scs"])

    monkeypatch.setattr(client, "send", send)
    with pytest.raises(PreflightRequired) as error:
        client.prepare_operation("run_simulation_setup", {"setup_ref": "setup:review"}, {})
    assert {r["field"] for r in error.value.missing} == {
        "targets/presentation",
        "libraries",
        "directories",
        "model_files",
        "directories/simulator_executable",
    }
    assert calls == ["aiCopilotDescribe"]
    client.requirements.update(
        presentation="foreground",
        targets=[
            dict(library="Work", cell="Tb", view="maestro", intent="read",
                 oa_view_type="maestro")
        ],
        libraries=[dict(library="Work", expected_path=expected[5])],
        directories=[
            dict(purpose="project", path=str(tmp_path)),
            dict(purpose="run", path=expected[6]),
        ],
        model_files=["/models/test.scs"],
        simulator_executable="/bin/spectre",
    )
    monkeypatch.setattr(
        "sico.adapters.circuit.call_project", lambda *a: dict(checks_passed=True, session={})
    )
    assert (
        client.prepare_operation("run_simulation_setup", {"setup_ref": "setup:review"}, {})[
            "expected"
        ]
        == expected
    )


@pytest.mark.parametrize("signal_type", ["net", "terminal", "terminalV"])
def test_signal_type_is_explicit_and_only_applies_to_signals(signal_type):
    spec = recipe()
    spec["tests"][0]["outputs"][1]["signal_type"] = signal_type
    if signal_type != "net":
        spec["tests"][0]["outputs"][1]["value"] = "/V1/PLUS"
    preview_recipe(spec)
    assert native_recipe(spec)[3][0][6][1][-1] == signal_type
    spec["tests"][0]["outputs"][0]["signal_type"] = signal_type
    with pytest.raises(CircuitSpecError, match="only supported for kind=signal"):
        preview_recipe(spec)


def _failing_registry(effect):
    def handler(inputs, context):
        raise RuntimeError("canonical value contains NaN")

    registry = ToolRegistry()
    registry.register(Tool("fail_with_reason", "test failure", {"type": "object"},
                           lambda inputs: None, handler, effect=effect))
    return registry


def test_read_failure_message_carries_the_exception_reason():
    result = _failing_registry("read").execute(
        ToolCall("call", "fail_with_reason", {}),
        BoundContext("i", "g", "bound", {}),
    )
    assert result.status == "tool_error"
    assert "Tool failed (RuntimeError): canonical value contains NaN" in result.summary


def test_write_failure_message_carries_the_reason_and_keeps_the_no_replay_rule():
    result = _failing_registry("write").execute(
        ToolCall("call", "fail_with_reason", {}),
        BoundContext("i", "g", "bound", {}),
    )
    assert result.status == "needs_reconcile"
    assert "工具异常（RuntimeError）：canonical value contains NaN" in result.summary
    assert "不会自动重放" in result.summary
