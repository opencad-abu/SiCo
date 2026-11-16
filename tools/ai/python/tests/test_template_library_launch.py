"""Project-root propagation, shared Symbol consumption and bundled install payload."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from test_template_symbol import example_record, plan_args

from cadai.launch import build_claude_mcp_config, build_codex_command
from cadai.runtime import RuntimePaths
from cadai.template_build import publish
from cadai.template_catalog import TemplateCatalog
from cadai.template_locations import builtin_root, template_root
from cadai.template_symbol import call_symbol


@pytest.mark.parametrize("value", ["", "/enterprise/templates with spaces"])
def test_new_environment_forwarded_to_codex_and_claude(tmp_path, value):
    env = {
        "SICO_CIRCUIT_TEMPLATES_DIR": value,
        "CAD_CIRCUIT_TEMPLATES_DIR": "obsolete-relative",
        "CAD_AI_TEMPLATE_ROOT": "obsolete-relative",
    }
    command = build_codex_command(
        "/bin/codex", "/bin/python3", tmp_path, tmp_path, 300, environment=env
    )
    config = next(arg for arg in command if arg.startswith("mcp_servers.sico_ic.env_vars="))
    assert "SICO_CIRCUIT_TEMPLATES_DIR" in json.loads(config.split("=", 1)[1])
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    config = json.loads(
        build_claude_mcp_config("/bin/python3", tmp_path, runtime, "token", 300, environment=env)
    )
    forwarded = config["mcpServers"]["sico_ic"]["env"]
    assert forwarded["SICO_CIRCUIT_TEMPLATES_DIR"] == value
    assert "CAD_CIRCUIT_TEMPLATES_DIR" not in forwarded
    assert "CAD_AI_TEMPLATE_ROOT" not in forwarded


def test_legacy_template_names_still_forwarded(tmp_path):
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    for legacy, value in (
        ("CAD_CIRCUIT_TEMPLATES_DIR", "/legacy/templates"),
        ("CAD_AI_TEMPLATE_ROOT", "/older/templates"),
    ):
        env = {legacy: value}
        config = json.loads(
            build_claude_mcp_config("/bin/python3", tmp_path, runtime, "token", 300, environment=env)
        )
        forwarded = config["mcpServers"]["sico_ic"]["env"]
        assert forwarded[legacy] == value


@pytest.mark.parametrize("value", ["relative/path", "/bad\npath"])
def test_optional_project_path_is_forwarded_for_deferred_validation(tmp_path, value):
    env = {"SICO_CIRCUIT_TEMPLATES_DIR": value}
    assert build_codex_command("/bin/codex", "/bin/python3", tmp_path, tmp_path, 300, environment=env)
    runtime = RuntimePaths(tmp_path, tmp_path / "socket", tmp_path / "spool")
    config = json.loads(build_claude_mcp_config(
        "/bin/python3", tmp_path, runtime, "token", 300, environment=env))
    assert config["mcpServers"]["sico_ic"]["env"]["SICO_CIRCUIT_TEMPLATES_DIR"] == value


def test_shared_symbol_template_writes_only_local_preview(tmp_path, monkeypatch):
    shared, workspace = tmp_path / "shared", tmp_path / "workspace"
    workspace.mkdir()
    record = example_record()
    publish([record], shared / "catalogs/symbol.sqlite3")
    monkeypatch.setenv("SICO_CIRCUIT_TEMPLATES_DIR", str(shared))

    class Client:
        def call(self, *args):
            return True, {"ok": True, "value": json.dumps({"ok": True})}

    out = call_symbol(
        "preview_template_symbol",
        {
            **plan_args(),
            "template_ref": record["template_ref"],
            "task_ref": "task:private",
            "request_id": "preview",
        },
        workspace=workspace,
        client=Client(),
    )
    assert out["ok"] and not (shared / "previews").exists()
    assert all(template_root(workspace) in Path(a["path"]).parents for a in out["artifacts"])


def test_cmake_installs_declared_builtin_catalog_and_loads_it(tmp_path):
    if not shutil.which("cmake"):
        pytest.skip("cmake unavailable")
    ai = Path(__file__).resolve().parents[2]
    cmake = (ai / "terminal/CMakeLists.txt").read_text()
    statements = [
        s
        for s in re.findall(r"install\([^)]*\)", cmake, re.DOTALL)
        if "reference/circuit_templates" in s
    ]
    assert len(statements) == 4
    source, build, destination = tmp_path / "source", tmp_path / "build", tmp_path / "installed-ai"
    source.mkdir()
    (source / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(template_payload NONE)\n"
        + 'set(_ai_source_root "'
        + str(ai)
        + '")\n'
        + "\n".join(statements)
        + "\n"
    )
    for command in (
        [
            "cmake",
            "-S",
            str(source),
            "-B",
            str(build),
            "-DCMAKE_INSTALL_PREFIX=" + str(destination),
        ],
        ["cmake", "--install", str(build)],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    installed = destination / "reference/circuit_templates"
    # The declared catalogs are the legacy records; the builtin tier may also
    # carry an installed standard payload next to them.
    before = TemplateCatalog(builtin_root()).query(
        {"offset": 0, "limit": 10, "data_format": "legacy"}
    )
    after = TemplateCatalog(installed).query(
        {"offset": 0, "limit": 10, "data_format": "legacy"}
    )
    assert [t["template_ref"] for t in before["templates"]] == [
        t["template_ref"] for t in after["templates"]
    ]
    # Six reviewed references are excluded from the library (EXCLUSIONS.json).
    assert before["total"] == after["total"] == 1142
    reports = json.loads((installed / "training/SOURCES.json").read_text())
    catalogs = {p["catalog"] for r in reports for p in r["publication"]["catalogs"]}
    # Every committed builtin catalog must be installed; the CMake list is the
    # only other place that enumerates them, so compare against the source tree.
    source_catalogs = {
        "catalogs/" + path.name
        for path in (ai / "reference/circuit_templates/catalogs").iterdir()
        if path.is_file()
    }
    assert {str(p.relative_to(installed)) for p in installed.rglob("*") if p.is_file()} == {
        "README.md",
        "build_builtin.py",
        "training/README.md",
        "training/NOTICE.md",
        "training/build_catalogs.py",
        "training/SOURCES.json",
        "custom_gates/README.md",
        "custom_gates/NOTICE.md",
        "custom_gates/SOURCES.json",
        "custom_gates/build_catalog.py",
        *source_catalogs,
        *catalogs,
        *("training/" + r["id"] + ".json" for r in reports),
    }
