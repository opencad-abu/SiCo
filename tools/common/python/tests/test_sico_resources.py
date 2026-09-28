"""Relocated resource lookup must reject mixed installations and CWD decoys."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import sicoresources


ROOT = Path(__file__).resolve().parents[4]


def test_resources_survive_readonly_relocation_and_ignore_working_directory(tmp_path):
    original = tmp_path / "original"
    common = original / "tools/common/python"
    common.mkdir(parents=True)
    (original / "bin").mkdir()
    shutil.copytree(ROOT / "etc", original / "etc")
    shutil.copytree(ROOT / "share", original / "share")
    for module in ("sicoresources", "sicopaths", "sicoenv"):
        shutil.copyfile(ROOT / "tools/common/python" / (module + ".py"), common / (module + ".py"))
    moved = tmp_path / "relocated prefix"
    original.rename(moved)
    decoy = tmp_path / "working/share"
    decoy.mkdir(parents=True)
    (decoy / "logo-dark.png").write_bytes(b"not the installed icon")
    external = tmp_path / "terminal-only"
    external.mkdir()
    (external / "selected.keytab").write_text("keymap fixture")
    for path in moved.rglob("*"):
        path.chmod(0o555 if path.is_dir() else 0o444)
    code = '''
from pathlib import Path
from sicoresources import icon, terminal_resource
import sys
root = Path(sys.argv[1])
assert icon("brand", "logo-dark.png") == root / "share/sico/icons/brand/logo-dark.png"
assert terminal_resource("selected.keytab") == Path(sys.argv[2]) / "selected.keytab"
assert icon("actions", "close-flow.png").read_bytes() != icon("actions", "close-agent.png").read_bytes()
print("SICO_RELOCATED_RESOURCES_OK")
'''
    env = {k: v for k, v in os.environ.items()
           if k not in {"CAD_HOME", "SICO_HOME", "SICO_ROOT", "CAD_AGENT_ROOT"}}
    env.update(PYTHONPATH=str(moved / "tools/common/python"), PYTHONDONTWRITEBYTECODE="1",
               SICO_AI_QTERMWIDGET_DATA_PATH=str(external))
    result = subprocess.run([sys.executable, "-s", "-c", code, str(moved), str(external)],
                            cwd=decoy.parent, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "SICO_RELOCATED_RESOURCES_OK" in result.stdout


def test_explicit_empty_terminal_data_does_not_fall_back(tmp_path):
    with pytest.raises(ValueError, match="absolute directory"):
        sicoresources.terminal_resource("default.keytab", {
            "SICO_AI_QTERMWIDGET_DATA_PATH": "", "CAD_AI_QTERMWIDGET_DATA_PATH": str(tmp_path)})


def test_icon_symlink_cannot_escape_installation(tmp_path, monkeypatch):
    from sicopaths import IDENTITY_BYTES, MARKER
    root = tmp_path / "installation"
    (root / MARKER).parent.mkdir(parents=True)
    (root / MARKER).write_bytes(IDENTITY_BYTES)
    (root / "bin").mkdir()
    (root / "tools/common").mkdir(parents=True)
    icons = root / "share/sico/icons"
    icons.mkdir(parents=True)
    (icons / "brand").symlink_to(tmp_path)
    from sicopaths import Installation
    monkeypatch.setattr(sicoresources, "current", lambda env: Installation(root))
    with pytest.raises(ValueError, match="escapes"):
        sicoresources.icon("brand", "logo.png")
