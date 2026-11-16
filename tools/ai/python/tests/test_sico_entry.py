"""The primary command keeps one installation and one CLI after relocation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import sico.cli as cli
from sico.service import local_startup

ROOT = Path(__file__).resolve().parents[4]


def test_default_command_opens_the_connection_desktop(monkeypatch, tmp_path):
    received = []
    monkeypatch.setattr(local_startup, "run_local_gui", lambda args: received.append(args) or 0)
    assert cli.main(["--launch-dir", str(tmp_path)]) == 0
    assert received[0].command == "connect" and received[0].gui


def test_relocated_source_command_and_module_share_history(tmp_path):
    product = tmp_path / "relocated product"
    for relative in ("tools/sico/python", "tools/ai/python/cadai", "tools/common/python"):
        shutil.copytree(ROOT / relative, product / relative,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for relative in ("tools/common/sh/sico-installation.sh", "tools/sico/bin/sico",
                     "bin/sico-tool-dispatch",
                     "etc/config/sico-install.json"):
        target = product / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    (product / "bin/sico").symlink_to("sico-tool-dispatch")
    project = tmp_path / "project"
    project.mkdir()
    env = {"PATH": os.environ["PATH"], "SICO_HOME": str(product),
           "SICO_PYTHON": sys.executable, "PYTHONPATH": "/untrusted/source",
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run(command):
        result = subprocess.run(command, cwd=project, env=env, capture_output=True,
                                text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        return result

    result = run([str(product / "bin/sico"), "demo", "--session", "relocated",
                  "--launch-dir", str(project), "--message", "Inspect this context"])
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert events[-1]["kind"] == "task.completed"
    env["PYTHONPATH"] = str(product / "tools/sico/python")
    history = run([sys.executable, "-s", "-m", "sico", "history", "--session", "relocated"])
    stored = [json.loads(line) for line in history.stdout.splitlines()]
    assert [row for row in stored if row["kind"] != "router.status"] == events
    assert not (ROOT / "tools/ai/python/cad-ai-agent").exists()
    assert not (product / ".cad").exists()
