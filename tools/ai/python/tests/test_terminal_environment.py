from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import cadai.terminal_environment as terminal_environment_module
from cadai.launch import prepare_terminal_environment, sanitized_terminal_environment
from cadai.lsf import LSF_HANDOFF_ENV, LSF_SUBMIT_DISPLAY_ENV, LSF_XFORWARD_ENV
from cadai.runtime import RuntimePaths

_REAL_LOCALE_SUPPORTS_UTF8 = terminal_environment_module._locale_supports_utf8


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "node-local" / "cad-ai-test"
    spool = root / "spool"
    bridge = tmp_path / "shared" / f".cad-ai-runtime-{os.getuid()}" / "session-test"
    spool.mkdir(parents=True, mode=0o700)
    bridge.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    bridge.parent.chmod(0o700)
    bridge.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool, bridge_spool=bridge)


@pytest.fixture(autouse=True)
def deterministic_input_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "DISPLAY",
        "XAUTHORITY",
        "XMODIFIERS",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "QT_IM_MODULE",
        "DBUS_SESSION_BUS_ADDRESS",
        "IBUS_ADDRESS",
        "SICO_AI_UTF8_LOCALE",
        "SICO_AI_PYQT_ROOT",
        "SICO_AI_PYTHON_LIBRARY_PATH",
        "SICO_AI_QT_PLUGIN_PATH",
        "SICO_SYSTEM_LD_LIBRARY_PATH",
        "SICO_CODEX_PYTHON_LIBRARY_PATH",
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SQLITE_TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        LSF_HANDOFF_ENV,
        LSF_SUBMIT_DISPLAY_ENV,
        LSF_XFORWARD_ENV,
        "LSB_JOBID",
        "LSB_HOSTS",
        "LSF_ENVDIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        terminal_environment_module,
        "_locale_supports_utf8",
        lambda value, _environment: value in {"C.UTF-8", "zh_CN.UTF-8"},
    )


def test_terminal_environment_removes_cadence_qt_pollution(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/cadence/python")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/cadence/qt")
    monkeypatch.setenv("QT_XCB_GL_INTEGRATION", "glx")
    monkeypatch.setenv("LD_PRELOAD", "/cadence/lib.so")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("IBUS_ADDRESS", "unix:path=/run/user/1000/ibus/bus")
    monkeypatch.setenv("SICO_AI_SYSTEM_LD_LIBRARY_PATH", "/system/qt/lib")
    monkeypatch.setenv("SICO_AI_QT_PLUGIN_PATH", "/system/qt/plugins")
    monkeypatch.setenv("SICO_CODEX_API_KEY", "private-api-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-only-secret")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "claude-oauth-secret")
    monkeypatch.setenv("SICO_CLAUDE_MODEL", "claude-only-model")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, codex_home)

    assert "PYTHONPATH" not in env
    assert env["QT_PLUGIN_PATH"] == "/system/qt/plugins"
    assert env["QT_XCB_GL_INTEGRATION"] == "none"
    assert "LD_PRELOAD" not in env
    assert "LC_ALL" not in env and env["LC_CTYPE"] == "C.UTF-8"
    assert env["QT_IM_MODULE"] == "ibus"
    assert env["XMODIFIERS"] == "@im=ibus"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["IBUS_ADDRESS"] == "unix:path=/run/user/1000/ibus/bus"
    assert env["LD_LIBRARY_PATH"] == ""
    assert env["SICO_AI_RUNTIME"] == str(runtime.root)
    assert env["SICO_AI_SOCKET"] == str(runtime.socket)
    assert env["SICO_AI_SPOOL"] == str(runtime.spool)
    assert env["SICO_AI_TOKEN"] == "secret"
    assert env["SICO_CODEX_API_KEY"] == "private-api-key"
    assert env["CODEX_HOME"] == str(codex_home)
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "SICO_CLAUDE_MODEL" not in env
    launch_temp = Path.cwd() / ".sico" / "ai"
    assert env["SICO_TEMP_DIR"] == str(launch_temp.parent)
    assert env["XDG_CACHE_HOME"] == str(launch_temp / "cache")
    assert env["XDG_RUNTIME_DIR"] == str(launch_temp / "runtime")
    assert (launch_temp / "runtime").stat().st_mode & 0o777 == 0o700
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_terminal_environment_uses_production_python_library_path(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SICO_SYSTEM_LD_LIBRARY_PATH", "/production/python/lib")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert env["LD_LIBRARY_PATH"] == "/production/python/lib"


def test_terminal_environment_ignores_project_pdk_mode(runtime, tmp_path, monkeypatch):
    from sico.agent_resources import mode

    mode.save(tmp_path, "generation", expected_revision=None)
    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)
    assert "SICO_PDK_WORKFLOW" not in env

    monkeypatch.setenv("SICO_PDK_WORKFLOW", "design")
    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)
    assert env["SICO_PDK_WORKFLOW"] == "design"


def test_terminal_environment_reads_project_model_settings(runtime, tmp_path):
    from sico.agent_resources import model

    model.save(tmp_path, "python", "openai", expected_revision=None)
    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)
    assert env["SICO_BACKEND"] == "python"
    assert env["SICO_PROVIDER"] == "openai"


def test_terminal_environment_reads_project_data_sources(runtime, tmp_path, monkeypatch):
    from sico.agent_resources import data_sources

    project_pdk, project_templates = tmp_path / "project-pdk", tmp_path / "project-templates"
    environment_pdk, environment_templates = tmp_path / "env-pdk", tmp_path / "env-templates"
    for path in (project_pdk, project_templates, environment_pdk, environment_templates):
        path.mkdir()
    monkeypatch.setenv("SICO_PDK_DATA", str(environment_pdk))
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(environment_templates))
    data_sources.save(
        tmp_path, str(project_pdk), None, expected_revision=None)

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert env["SICO_PDK_DATA"] == str(project_pdk)
    assert env["SICO_CIRCUIT_TEMPLATES_DIR"] == str(environment_templates)


def test_terminal_environment_prefers_ai_python_library_override(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SICO_SYSTEM_LD_LIBRARY_PATH", "/production/python/lib")
    monkeypatch.setenv("SICO_AI_PYTHON_LIBRARY_PATH", "/ai/python/lib")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert env["LD_LIBRARY_PATH"] == "/ai/python/lib"


def test_explicit_temp_root_survives_workspace_and_cwd_change(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    launch_temp = tmp_path / "launch" / ".sico" / "ai"
    launch_temp.parent.parent.mkdir()
    workspace = tmp_path / "selected-workspace"
    changed = tmp_path / "changed-cwd"
    workspace.mkdir()
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.setenv("SICO_TEMP_DIR", str(launch_temp.parent))

    env = sanitized_terminal_environment(
        runtime,
        "secret",
        workspace,
        tmp_path,
        temp_root=launch_temp.parent,
    )

    assert env["SICO_TEMP_DIR"] == str(launch_temp.parent)
    assert all(
        env[name] == str(launch_temp)
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )
    assert env["XDG_CACHE_HOME"] == str(launch_temp / "cache")
    assert env["XDG_RUNTIME_DIR"] == str(launch_temp / "runtime")
    assert not (workspace / ".cad").exists()
    assert not (changed / ".cad").exists()


def test_claude_environment_keeps_desktop_environment_but_hides_codex_credentials(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("SICO_CODEX_API_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("SICO_CODEX_API_KEY", "codex-only-secret")
    monkeypatch.setenv("SICO_CODEX_MODEL", "codex-only-model")
    monkeypatch.setenv("SICO_CODEX_HOME", "/state/sico-ai")
    monkeypatch.setenv("CODEX_HOME", "/state/codex-home")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-only-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-gateway.example/v1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-secret")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("IBUS_ADDRESS", "unix:path=/run/user/1000/ibus/bus")
    monkeypatch.setenv("SICO_AI_QT_PLUGIN_PATH", "/system/qt/plugins")
    monkeypatch.setenv("SICO_AI_PYQT_ROOT", "/source/ai/terminal/pyqt")
    monkeypatch.setenv("SICO_AI_PYTHON_LIBRARY_PATH", "/system/python/lib")
    monkeypatch.setenv("SICO_AI_PLATFORM", "rhel7")
    monkeypatch.setenv("SICO_AI_OS_RELEASE_FILE", "/test/os-release")

    env = sanitized_terminal_environment(runtime, "bridge-secret", tmp_path, None, "claude")

    assert "SICO_CODEX_API_BASE_URL" not in env
    assert "SICO_CODEX_API_KEY" not in env
    assert "SICO_CODEX_MODEL" not in env
    assert "SICO_AI_RUNTIME" not in env
    assert "SICO_AI_SOCKET" not in env
    assert "SICO_AI_SPOOL" not in env
    assert "SICO_AI_TOKEN" not in env
    assert "SICO_CODEX_HOME" not in env
    assert "CODEX_HOME" not in env
    assert "OPENAI_API_KEY" not in env
    assert "OPENAI_BASE_URL" not in env
    assert env["ANTHROPIC_API_KEY"] == "claude-secret"
    assert env["CLAUDE_CODE_NO_FLICKER"] == "1"
    assert env["MCP_TIMEOUT"] == "10000"
    assert env["QT_IM_MODULE"] == "ibus"
    assert env["XMODIFIERS"] == "@im=ibus"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["IBUS_ADDRESS"] == "unix:path=/run/user/1000/ibus/bus"
    assert env["QT_PLUGIN_PATH"] == "/system/qt/plugins"
    assert env["SICO_AI_QT_PLUGIN_PATH"] == "/system/qt/plugins"
    assert env["SICO_AI_PYQT_ROOT"] == "/source/ai/terminal/pyqt"
    assert env["SICO_AI_PYTHON_LIBRARY_PATH"] == "/system/python/lib"
    assert env["LD_LIBRARY_PATH"] == "/system/python/lib"
    assert env["QT_XCB_GL_INTEGRATION"] == "none"
    assert env["SICO_AI_PLATFORM"] == "rhel7"
    assert env["SICO_AI_OS_RELEASE_FILE"] == "/test/os-release"


def test_utf8_desktop_environment_is_not_rewritten(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    monkeypatch.setenv("QT_IM_MODULE", "ibus")
    monkeypatch.setenv("XMODIFIERS", "@im=ibus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("IBUS_ADDRESS", "unix:path=/run/user/1000/ibus/bus")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert env["LANG"] == "zh_CN.UTF-8"
    assert "LC_CTYPE" not in env
    assert env["QT_IM_MODULE"] == "ibus"
    assert env["XMODIFIERS"] == "@im=ibus"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["IBUS_ADDRESS"] == "unix:path=/run/user/1000/ibus/bus"


@pytest.mark.parametrize(
    ("available", "expected"),
    [
        ({"C.utf8"}, "C.utf8"),
        ({"en_US.utf8"}, "en_US.utf8"),
        ({"zh_CN.utf8"}, "zh_CN.utf8"),
    ],
)
def test_utf8_locale_selector_uses_an_available_rhel_locale(
    available: set[str], expected: str
) -> None:
    selected = terminal_environment_module._select_utf8_locale(
        {"LANG": "C"}, probe=lambda value: value in available
    )
    assert selected == expected


def test_valid_inherited_lc_all_is_preserved(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    monkeypatch.setenv("LANG", "C")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert env["LC_ALL"] == "zh_CN.UTF-8"
    assert "LC_CTYPE" not in env


def test_non_utf8_lc_all_is_replaced_by_available_lc_ctype(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        terminal_environment_module,
        "_locale_supports_utf8",
        lambda value, _environment: value == "en_US.utf8",
    )
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert "LC_ALL" not in env
    assert env["LC_CTYPE"] == "en_US.utf8"


def test_explicit_utf8_locale_overrides_inherited_locale(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        terminal_environment_module,
        "_locale_supports_utf8",
        lambda value, _environment: value == "zh_CN.utf8",
    )
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("SICO_AI_UTF8_LOCALE", "zh_CN.utf8")

    env = sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert "LC_ALL" not in env
    assert env["LC_CTYPE"] == "zh_CN.utf8"


@pytest.mark.parametrize(
    ("configured", "error"),
    [("C", ValueError), ("missing.UTF-8", RuntimeError)],
)
def test_invalid_explicit_utf8_locale_is_rejected(
    runtime: RuntimePaths,
    tmp_path: Path,
    monkeypatch,
    configured: str,
    error: type[Exception],
) -> None:
    monkeypatch.setenv("SICO_AI_UTF8_LOCALE", configured)

    with pytest.raises(error, match="SICO_AI_UTF8_LOCALE"):
        sanitized_terminal_environment(runtime, "secret", tmp_path, tmp_path)


def test_missing_or_timed_out_locale_probe_is_a_bounded_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        terminal_environment_module,
        "_locale_supports_utf8",
        _REAL_LOCALE_SUPPORTS_UTF8,
    )
    monkeypatch.setattr(terminal_environment_module, "_locale_executable", lambda: "/bin/locale")

    def time_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("locale", 2)

    monkeypatch.setattr(terminal_environment_module.subprocess, "run", time_out)
    assert terminal_environment_module._locale_supports_utf8("C.UTF-8", {}) is False

    monkeypatch.setattr(terminal_environment_module, "_locale_executable", lambda: None)
    assert terminal_environment_module._locale_supports_utf8("C.UTF-8", {}) is False


def test_no_available_utf8_locale_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="no usable UTF-8 locale"):
        terminal_environment_module._select_utf8_locale({"LANG": "C"}, probe=lambda _value: False)


def test_lsf_handoff_requires_remote_display(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(LSF_HANDOFF_ENV, "1")
    monkeypatch.setenv(LSF_SUBMIT_DISPLAY_ENV, ":0")
    monkeypatch.setenv(LSF_XFORWARD_ENV, "1")
    monkeypatch.setenv("LANG", "C")

    with pytest.raises(RuntimeError, match="LSF AI session has no DISPLAY"):
        prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path)


def test_lsf_handoff_warns_when_host_local_display_was_not_rewritten(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(LSF_HANDOFF_ENV, "1")
    monkeypatch.setenv(LSF_SUBMIT_DISPLAY_ENV, ":0")
    monkeypatch.setenv(LSF_XFORWARD_ENV, "1")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("LANG", "C")

    env, warnings = prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert warnings == (
        "LSF did not rewrite a host-local DISPLAY; verify that the job uses working X11 forwarding",
    )
    launch_temp = Path.cwd() / ".sico" / "ai"
    assert all(
        env[name] == str(launch_temp)
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
    )
    assert env["SICO_TEMP_DIR"] == str(launch_temp.parent)
    assert all(name not in env for name in (LSF_HANDOFF_ENV, LSF_SUBMIT_DISPLAY_ENV))


def test_lsf_handoff_accepts_rewritten_display_and_removes_metadata(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(LSF_HANDOFF_ENV, "1")
    monkeypatch.setenv(LSF_SUBMIT_DISPLAY_ENV, ":0")
    monkeypatch.setenv(LSF_XFORWARD_ENV, "1")
    monkeypatch.setenv("DISPLAY", "localhost:12.0")
    monkeypatch.setenv("LANG", "C")

    env, warnings = prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert warnings == ()
    assert all(name not in env for name in (LSF_HANDOFF_ENV, LSF_SUBMIT_DISPLAY_ENV))
    assert LSF_XFORWARD_ENV not in env


def test_lsf_handoff_warns_for_unreadable_xauthority(
    runtime: RuntimePaths, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(LSF_HANDOFF_ENV, "1")
    monkeypatch.setenv(LSF_SUBMIT_DISPLAY_ENV, ":0")
    monkeypatch.setenv(LSF_XFORWARD_ENV, "1")
    monkeypatch.setenv("DISPLAY", "localhost:12.0")
    monkeypatch.setenv("XAUTHORITY", str(tmp_path / "missing-xauthority"))
    monkeypatch.setenv("LANG", "C")

    _env, warnings = prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path)

    assert warnings == ("XAUTHORITY is set but is not a readable regular file",)


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_new_gui_settings_survive_terminal_handoff(runtime, tmp_path, monkeypatch, agent):
    monkeypatch.setenv("SICO_AI_PYTHON_LIBRARY_PATH", "")
    monkeypatch.setenv("CAD_AI_PYTHON_LIBRARY_PATH", "/old/python/lib")
    monkeypatch.setenv("SICO_SYSTEM_LD_LIBRARY_PATH", "/system/lib")
    monkeypatch.setenv("SICO_AI_QT_PLUGIN_PATH", "/new/plugins")
    monkeypatch.setenv("CAD_AI_QT_PLUGIN_PATH", "/old/plugins")
    env, _ = prepare_terminal_environment(runtime, "secret", tmp_path, tmp_path, agent)
    assert env["LD_LIBRARY_PATH"] == ""
    assert env["QT_PLUGIN_PATH"] == env["SICO_AI_QT_PLUGIN_PATH"] == "/new/plugins"
    assert env["SICO_AI_PYTHON_LIBRARY_PATH"] == ""
