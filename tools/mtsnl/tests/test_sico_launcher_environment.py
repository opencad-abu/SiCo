"""Exercise the real SKILL launcher and the standalone development entry."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]


def clean_environment(work):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CAD_", "SICO_", "MTS_", "PYTHON", "LD_", "QT_"))}
    env.update(SICO_HOME=str(ROOT), SICO_PYTHON=sys.executable, PWD=str(work))
    return env


def test_skill_launch_uses_shared_state_python_and_library_selection(tmp_path):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    output = tmp_path / "command.txt"
    code = f'''
load({json.dumps(str(ROOT / 'tools/mtsnl/skill/MTS_netlistor.il'))})
unless(mtsNetlistorPythonExe()=={json.dumps(sys.executable)} error("Python precedence"))
unless(mtsNetlistorToolsRoot()=={json.dumps(str(ROOT / 'tools'))} error("installation root"))
port=outfile({json.dumps(str(output))})
fprintf(port "%s" mtsNetlistorLaunchCommand("/session with spaces.json" 12345))
close(port)
setShellEnvVar("SICO_PYTHON" "")
when(errset(mtsNetlistorPythonExe() nil) error("empty Python admitted"))
setShellEnvVar("MTS_NETLISTOR_ROOT" "/foreign/tool")
when(errset(mtsNetlistorModuleRoot() nil) error("foreign tool admitted"))
printf("SICO_MTS_LAUNCH_OK\\n")
exit()
'''
    env = clean_environment(tmp_path)
    env.update(CAD_PYTHON="/missing/legacy", SICO_SYSTEM_LD_LIBRARY_PATH="",
               CAD_SYSTEM_LD_LIBRARY_PATH="/legacy/library", TMPDIR="/original/temp",
               QT_PLUGIN_PATH="/eda/qt", CDS_MPS_SESSION="old-session")
    result = subprocess.run([dbaccess], input=code, cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=45)
    log = result.stdout + result.stderr
    assert result.returncode == 0 and "SICO_MTS_LAUNCH_OK" in log and "*Error*" not in log, log
    tokens = shlex.split(output.read_text())
    entry = str(ROOT / "tools/mtsnl/bin/mts-netlistor")
    position = tokens.index(entry)
    assert tokens[position + 1:] == ["gui", "--session", "/session with spaces.json",
                                    "--parent-pid", "12345"]
    child = subprocess.run(tokens[1:position] + ["/usr/bin/env"], env=env,
                           capture_output=True, text=True, check=True)
    values = dict(line.split("=", 1) for line in child.stdout.splitlines() if "=" in line)
    assert values["SICO_PYTHON"] == sys.executable
    assert values["SICO_TEMP_DIR"] == values["TMPDIR"] == str(tmp_path / ".sico")
    assert values["SICO_ORIG_TMPDIR"] == "/original/temp"
    assert values["LD_LIBRARY_PATH"] == ""
    assert "QT_PLUGIN_PATH" not in values and "CDS_MPS_SESSION" not in values
    assert not (tmp_path / ".cad").exists()


def test_shell_entry_honors_python_root_and_rejects_foreign_module(tmp_path):
    python_root = tmp_path / "python root"
    (python_root / "bin").mkdir(parents=True)
    (python_root / "bin/python3").symlink_to(sys.executable)
    env = clean_environment(tmp_path)
    env.pop("SICO_PYTHON")
    env["SICO_PYTHON_ROOT"] = str(python_root)
    entry = ROOT / "tools/mtsnl/bin/mts-netlistor"
    result = subprocess.run([str(entry), "--help"], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and "gui" in result.stdout, result.stderr
    env["MTS_NETLISTOR_ROOT"] = str(tmp_path)
    result = subprocess.run([str(entry), "--help"], env=env, capture_output=True, text=True)
    assert result.returncode == 2 and "conflicts" in result.stderr
