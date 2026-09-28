"""defaults probe cases regressions."""

from __future__ import annotations
from pathlib import Path
from mtsnetlistor.defaults import (
    MaeSetup,
    render_defaults_probe_script,
)
from defaults_fixtures import (
    _protected_probe_source,
    _source,
)


def test_probe_script_observes_asi_before_user_overrides(tmp_path: Path) -> None:
    source = _source(tmp_path)
    script = render_defaults_probe_script(
        source,
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
        output_path=tmp_path / "probe" / "defaults.json",
    )
    assert "mtsRuntimeDefaultsAsi" in script and "loadContext(" in script
    assert "procedure(" not in script and "design(" not in script
    assert str(tmp_path / "probe" / "defaults.json") in script
    script = _protected_probe_source()
    assert script.index('  design(') > script.index("  tool=asiGetTool")
    assert script.index("baseline=if(") < script.index('  design(')
    assert '\\"baseline\\"' in script
    assert "asiGetEnvOptionVal" in script
    assert "modelPath" not in script
    assert '\\"environment_options\\":{}' in script
    assert "modelFile(" not in script
    assert "createNetlist(" not in script


def test_probe_script_reads_active_asi_session_and_model_selection_api(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    script = render_defaults_probe_script(
        source,
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
    )
    # The tool is still the baseline scope, but post-design snapshots must
    # resolve the active session first so PDK callbacks on spectre0 are seen.
    assert "mtsRuntimeDefaultsAsi" in script
    script = _protected_probe_source()
    assert "asiGetCurrentSession" in script
    assert "mtsDefaultsSnapshot(tool session)" in script
    assert "asiGetModelLibSelectionList" in script
    assert "asiGetModelLibFile" in script
    assert "asiGetModelLibSection" in script
    assert '\\"raw_model_files\\"' in script
    assert '\\"model_files_source\\"' in script
    assert '\\"session_available\\"' in script
    assert "modelPath" not in script


def test_mae_probe_script_uses_explicit_mae_apis_without_guessing(tmp_path: Path) -> None:
    source = _source(tmp_path)
    script = render_defaults_probe_script(
        source,
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
        provider="mae_test",
        mae_setup=MaeSetup("ade", "setup", "maestro", "setup:inv:1", history="Interactive.2"),
    )
    assert '"ade" "setup" "maestro" "setup:inv:1" nil "Interactive.2"' in script
    assert "mtsRuntimeDefaultsMae" in script and "maeOpenSetup" not in script
    script = _protected_probe_source()
    assert "apply('maeOpenSetup append(list(maeLibrary maeCell maeView)" in script
    assert 'append(when(maeHistory list(?histName maeHistory)) list(?mode "r"))' in script
    assert "maeGetEnvOption" in script
    assert "maeGetSimOption" in script
    assert '\\"provider\\":' in script
    assert "reportSuffix)" in script
    assert "maeGetSetup()" not in script


def test_mae_probe_script_reports_open_and_api_failures_without_masquerading_as_success(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    script = render_defaults_probe_script(
        source,
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
        provider="mae_test",
        mae_setup=MaeSetup("ade", "setup", "maestro", "setup:inv:1"),
    )
    assert "mtsRuntimeDefaultsMae" in script
    script = _protected_probe_source()
    assert "maeOpenAnswer=errset(apply('maeOpenSetup" in script
    assert "maeOpenSetup returned nil (setup does not exist or could not be opened)" in script
    assert "maeGetEnvOption raised an exception:" in script
    assert "maeGetSimOption raised an exception:" in script
    assert 'list(t nil nil "maeGetEnvOption returned nil")' in script
    assert 'list(t nil nil "maeGetSimOption returned nil")' in script
    assert 'reportStatus=if(maeApiErrors then \"failed\" else \"succeeded\")' in script
    assert '",\\"diagnostics\\":" mtsDefaultsJsonArrayValue(maeDiagnostics)' in script
    assert '",\\"api_errors\\":" mtsDefaultsJsonArrayValue(maeApiErrors)' in script
    assert '\\"model_files\\":[],\\"environment_options\\":{},\\"simulator_options\\":{}' in script


def test_mae_probe_closes_explicit_session_without_masking_body_failures(
    tmp_path: Path,
) -> None:
    script = render_defaults_probe_script(
        _source(tmp_path),
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
        provider="mae_test",
        mae_setup=MaeSetup("ade", "setup", "maestro", "nominal"),
    )
    assert '"ade" "setup" "maestro" "nominal" nil nil' in script
    script = _protected_probe_source()
    assert 'list(?mode "r"))))) nil)' in script
    assert "unwindProtect(" in script
    assert "errset(maeCloseSession(?session session) nil)" in script
    assert "maeCloseSession(session)" not in script
    assert "maeCloseSession returned nil" in script
    assert "MAE defaults body raised an exception:" in script
    assert "maeCleanupAnswer=errset(" in script
    assert "MAE cleanup raised an exception:" in script
    assert "  tool=nil" in script
    protected = script[script.index("unwindProtect(") :]
    assert protected.index("maeBodyAnswer=errset(") < protected.index(
        "maeCloseDiagnostic=mtsDefaultsMaeClose(maeSession)"
    )
    assert "          ), maeBodyError=errset.errset" in protected


def test_mae_probe_script_supports_flat_and_pair_property_list_forms(tmp_path: Path) -> None:
    script = render_defaults_probe_script(
        _source(tmp_path),
        "spectre",
        project_dir=tmp_path / "probe",
        result_dir=tmp_path / "probe",
        provider="mae_test",
        mae_setup=MaeSetup("ade", "setup", "maestro", "setup:inv:1"),
    )
    assert "mtsRuntimeDefaultsMae" in script
    script = _protected_probe_source()
    assert "documented MAE form is (name value name value ...)" in script
    assert "out=cons(list(mtsDefaultsMaeKeyText(car(item)) cadr(item)) out)" in script
    assert "out=cons(list(mtsDefaultsMaeKeyText(key) value) out)" in script
    assert "mtsDefaultsJsonArrayValue(modelFiles)" in script
