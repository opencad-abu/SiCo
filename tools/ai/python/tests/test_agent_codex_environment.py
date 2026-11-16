"""Source SKILL capture and actual Codex MCP child environment, using synthetic values."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import wait_until
from sico.codex.backend import CodexBackend
from sico.service.launch import desktop_environment
from sico.storage.journal import SessionJournal
from test_agent_codex_native import (  # noqa: F401
    CONTEXT,
    gateway,
    native_codex,
    registry,
    settings,
)

CAD = Path(__file__).absolute().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
CAPTURED = {
    "PATH": "/site/IC231/bin:/usr/bin:/bin",
    "LD_LIBRARY_PATH": "/site/IC231/lib",
    "PYTHONPATH": "/site/IC231/python",
    "MODULEPATH": "/site/modulefiles with 'quotes'/$literal",
    "LOADEDMODULES": "IC23.10.130:LSF",
    "_LMFILES_": "/site/modulefiles/IC231:/site/modulefiles/LSF",
    "MODULESHOME": "/site/modules",
    "LMOD_CMD": "/site/lmod/libexec/lmod",
}


def test_mcp_environment_includes_project_pdk_mode():
    from sico.codex.runtime import mcp_environment_vars

    assert "SICO_PDK_WORKFLOW" in mcp_environment_vars(
        {"SICO_PDK_WORKFLOW": "generation"}
    )


def test_runtime_restores_project_data_sources_after_sico_strip(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sico.agent_resources import data_sources
    from sico.codex import runtime as runtime_module
    from sico.codex.settings import CodexSettings
    from sico.storage.journal import SessionJournal

    pdk, templates = tmp_path / "pdk", tmp_path / "templates"
    pdk.mkdir()
    templates.mkdir()
    data_sources.save(tmp_path, str(pdk), str(templates), expected_revision=None)
    captured = {}

    class App:
        def __init__(self, command, environment, _cwd, **_kwargs):
            captured["environment"] = environment
            self.child = SimpleNamespace(returncode=0)

        def close(self):
            pass

    monkeypatch.setattr(runtime_module, "AppServer", App)
    monkeypatch.setattr(runtime_module.process_monitor, "track", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_module.process_monitor, "finish", lambda *_a, **_k: None)
    settings = CodexSettings(
        dict(provider="responses", base_url="https://upstream.example/v1", model="m"),
        {"SICO_API_KEY": "upstream-private"},
    )
    monkeypatch.setattr(settings, "executable", lambda: "/bin/true")
    with SessionJournal(tmp_path, "data-sources") as journal:
        process = runtime_module.CodexRuntime(settings, journal, None)
        process.close()
    assert captured["environment"]["SICO_PDK_DATA"] == str(pdk)
    assert captured["environment"]["SICO_CIRCUIT_TEMPLATES_DIR"] == str(templates)


@pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")
@pytest.mark.parametrize("empty_unset", [False, True])
def test_skill_launcher_captures_before_sanitizing_paths(tmp_path, empty_unset):
    output = tmp_path / "command.txt"
    source = tmp_path / "capture.il"
    source.write_text(
        f"sicoRoot={json.dumps(str(CAD / 'sico'))}\n"
        f"load({json.dumps(str(CAD / 'common/skill/SICO_environment.il'))})\n"
        f"load({json.dumps(str(CAD / 'common/skill/SICO_installation.il'))})\n"
        f"load({json.dumps(str(CAD / 'sico/skill/SICO_launcher.il'))})\n"
        + "\n".join(
            f"setShellEnvVar({json.dumps(key)} {json.dumps(value)})"
            for key, value in CAPTURED.items()
        )
        + ('\nsetShellEnvVar("MODULEPATH" "") unsetShellEnvVar("PYTHONPATH")\n'
           if empty_unset else '')
        + '\nsetShellEnvVar("SICO_VIRTUOSO_LD_LIBRARY_PATH_SET" "0")\n'
        + '\nsetShellEnvVar("SICO_AI_PYTHON_LIBRARY_PATH" "/copilot/python/lib")\n'
        + f"capturePort=outfile({json.dumps(str(output))})\n"
        + 'fprintf(capturePort "%s" sicoLaunchCommand("quick-input"))\n'
        + 'close(capturePort) printf("CAPTURE_PASS\\n") exit()\n'
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT", "CAD_HOME"}
    }
    env["SICO_HOME"] = str(CAD.parent)
    env["SICO_PYTHON"] = "/software/pkgs/python/3.9.13/bin/python3"
    result = subprocess.run(
        [str(DB), "-load", str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert "CAPTURE_PASS" in result.stdout, result.stdout + result.stderr
    assert "*Error*" not in result.stdout + result.stderr
    arguments = shlex.split(output.read_text())
    assignments = dict(arg.split("=", 1) for arg in arguments if "=" in arg)
    for key, value in CAPTURED.items():
        expected = "" if empty_unset and key in {"PYTHONPATH", "MODULEPATH"} else value
        assert assignments["SICO_VIRTUOSO_" + key] == expected
        assert assignments["SICO_VIRTUOSO_" + key + "_SET"] == (
            "0" if empty_unset and key == "PYTHONPATH" else "1")
    assert assignments["LD_LIBRARY_PATH"] == "/copilot/python/lib"
    assert arguments[-3:] == ["quick-input", "--launch-dir", str(tmp_path)]


def mcp_child_environment(pid):
    """Inspect only descendants of this test-owned app-server process."""
    folder = Path("/proc") / str(pid)
    try:
        if b"sico.codex.mcp" in (folder / "cmdline").read_bytes().split(b"\0"):
            values = dict(
                entry.decode().split("=", 1)
                for entry in (folder / "environ").read_bytes().split(b"\0")
                if b"=" in entry
            )
            selected = {
                "TMPDIR",
                "TMP",
                "TEMP",
                "SQLITE_TMPDIR",
                "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR",
                "CAD_TEMP_DIR",
                "SICO_TEMP_DIR",
                "SICO_PYTHON",
                "SICO_API_KEY",
                "CAD_COPILOT_MODEL_KEY",
                "CAD_VIRTUOSO_SECRET",
                *("CAD_VIRTUOSO_" + key for key in CAPTURED),
            }
            return {key: value for key, value in values.items() if key in selected}
        children = {
            child
            for task in (folder / "task").iterdir()
            for child in (task / "children").read_text().split()
        }
    except FileNotFoundError:
        return None
    for child in children:
        found = mcp_child_environment(child)
        if found:
            return found
    return None


def test_native_mcp_inherits_snapshot_and_nas_alias_without_model_key(
    tmp_path,
    native_codex,  # noqa: F811
    gateway,  # noqa: F811
):
    physical = tmp_path / "volume"
    physical.mkdir()
    shared = tmp_path / "shared-nas"
    shared.symlink_to(physical, target_is_directory=True)
    provider = settings(gateway, native_codex, "flat")
    provider.environment.update({"CAD_VIRTUOSO_" + k: v for k, v in CAPTURED.items()})
    provider.environment.update(
        CAD_TEMP_DIR="/inaccessible/stale/.cad/ai",
        CAD_VIRTUOSO_SECRET="not-a-captured-module-variable",
        LOADEDMODULES="agent-python",
    )
    provider.environment = desktop_environment(shared, "env", provider.environment)
    with SessionJournal(shared, "env") as journal:
        backend = CodexBackend(provider, registry([]), journal, CONTEXT)
        try:
            events = list(backend.initialize_context(lambda: False, context=CONTEXT, input_id="i"))
            assert events[-1]["kind"] == "task.completed", events
            child_env = wait_until(lambda: mcp_child_environment(backend.runtime.rpc.child.pid), 5)
            for key, value in CAPTURED.items():
                assert child_env["CAD_VIRTUOSO_" + key] == value
            assert "CAD_VIRTUOSO_SECRET" not in child_env
            assert "SICO_API_KEY" not in child_env and "CAD_COPILOT_MODEL_KEY" not in child_env
            assert "copilot-test-secret" not in child_env.values()
            assert child_env["SICO_TEMP_DIR"] == str(shared / ".sico")
            assert "CAD_TEMP_DIR" not in child_env
            for key in (
                "TMPDIR",
                "TMP",
                "TEMP",
                "SQLITE_TMPDIR",
                "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR",
            ):
                assert Path(child_env[key]).parent == journal.directory / "codex/tmp"
                assert Path(child_env[key]).name.startswith("runtime-")
            assert child_env["SICO_PYTHON"] == provider.environment["SICO_PYTHON"]
            assert not gateway.requests
        finally:
            backend.close()
