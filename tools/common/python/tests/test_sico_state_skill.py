"""Real SKILL IPC delegates project selection to the Python state authority."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("legacy", [False, True])
def test_skill_state_owner_preserves_launch_and_rejects_parallel_root(tmp_path, legacy):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    launch = tmp_path / "launch with spaces"
    launch.mkdir()
    changed = tmp_path / "changed"
    changed.mkdir()
    state = launch / (".cad" if legacy else ".sico")
    if legacy:
        state.mkdir(mode=0o700)
    output = tmp_path / "command"
    code = "\n".join(
        f'load({json.dumps(str(ROOT / "tools/common/skill" / name))})'
        for name in ("SICO_environment.il", "SICO_installation.il", "SICO_state.il")
    ) + f'''
changeWorkingDir({json.dumps(str(changed))})
unless(SICO_stateDirectory()=={json.dumps(str(state))} error("project root mismatch"))
unless(SICO_stateDirectory("ai")=={json.dumps(str(state / 'ai'))} error("AI root mismatch"))
port=outfile({json.dumps(str(output))})
fprintf(port "%s" SICO_stateEnvironment())
close(port)
printf("SICO_STATE_SUCCESS_END\\n")
createDir({json.dumps(str(launch / ('.sico' if legacy else '.cad')))})
when(errset(SICO_stateDirectory() nil) error("coexisting roots admitted"))
printf("SICO_STATE_SKILL_OK\\n")
exit()
'''
    if legacy:
        code = code[:code.index('changeWorkingDir(')] + '\nwhen(errset(SICO_stateDirectory() nil) error("unmigrated state accepted"))\nprintf("SICO_STATE_SKILL_OK\\n")\nexit()\n'
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CAD_", "SICO_", "PYTHON", "LD_"))}
    environment.update(SICO_HOME=str(ROOT), SICO_PYTHON="/software/pkgs/python/3.9.13/bin/python3",
                       PWD=str(launch), TMPDIR="/vendor/temp")
    result = subprocess.run([dbaccess], input=code, text=True, capture_output=True,
                            cwd=launch, env=environment, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SICO_STATE_SKILL_OK" in result.stdout, result.stdout + result.stderr
    assert "*Error*" not in result.stderr, result.stdout + result.stderr
    if legacy:
        assert list(state.iterdir()) == []
        assert not (launch / ".sico").exists()
        assert not output.exists()
        return
    assert output.is_file()
    assert output.stat().st_size, result.stdout + result.stderr
    assert not (changed / ".sico").exists()
    restored = subprocess.run(output.read_text() + " /usr/bin/env", shell=True,
                              capture_output=True, text=True, env=environment, check=True)
    rows = dict(line.split("=", 1) for line in restored.stdout.splitlines() if "=" in line)
    assert rows["TMPDIR"] == str(state)
    assert rows["SICO_TEMP_DIR"] == str(state)
    assert rows["SICO_ORIG_TMPDIR"] == "/vendor/temp"


def test_ai_source_loader_uses_new_environment_and_rejects_stale_pwd(tmp_path):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    launch = tmp_path / "launch"
    stale = tmp_path / "stale"
    launch.mkdir()
    stale.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CAD_", "SICO_", "PYTHON", "LD_"))}
    python = "/software/pkgs/python/3.9.13/bin/python3"
    environment.update(SICO_HOME=str(ROOT), SICO_PYTHON=python, PWD=str(stale),
                       CAD_PYTHON="/missing/legacy-python")
    code = f'''
load({json.dumps(str(ROOT / 'tools/ai/skill++/AI.ils'))})
unless(aiResolveRoot()=={json.dumps(str(ROOT / 'tools/ai'))} error("new installation root"))
unless(aiPythonExecutable()=={json.dumps(python)} error("new Python precedence"))
unless(aiLaunchWorkingDir=={json.dumps(str(launch))} error("stale PWD admitted"))
unless(aiWorkflowLaunchDir==aiLaunchWorkingDir error("flow launch disagrees"))
changeWorkingDir({json.dumps(str(stale))})
unless(SICO_stateLaunchDirectory()==aiLaunchWorkingDir error("launch moved"))
setShellEnvVar("SICO_PYTHON" "")
when(aiPythonExecutable() error("empty new Python fell back to legacy"))
printf("SICO_AI_NEW_ENVIRONMENT_OK\\n")
exit()
'''
    result = subprocess.run([dbaccess], input=code, env=environment, cwd=launch,
                            text=True, capture_output=True, timeout=45)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "SICO_AI_NEW_ENVIRONMENT_OK" in output, output
    assert "*Error*" not in output, output
    assert not (stale / ".sico").exists()


@pytest.mark.parametrize("overrides", [{"SICO_PYTHON_ROOT": ""},
                                      {"SICO_PYTHON": "relative-python"}])
def test_skill_state_rejects_invalid_explicit_python_before_writing(tmp_path, overrides):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    (tmp_path / "relative-python").symlink_to("/software/pkgs/python/3.9.13/bin/python3")
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CAD_", "SICO_", "PYTHON", "LD_"))}
    environment.update(SICO_HOME=str(ROOT), PWD=str(tmp_path), **overrides)
    code = "\n".join(
        f'load({json.dumps(str(ROOT / "tools/common/skill" / name))})'
        for name in ("SICO_environment.il", "SICO_installation.il", "SICO_state.il")
    ) + '''
when(errset(SICO_stateDirectory() nil) error("invalid Python admitted"))
printf("SICO_STATE_INVALID_PYTHON_OK\\n")
exit()
'''
    result = subprocess.run([dbaccess], input=code, env=environment, cwd=tmp_path,
                            text=True, capture_output=True, timeout=45)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "SICO_STATE_INVALID_PYTHON_OK" in output, output
    assert "*Error*" not in output, output
    assert not (tmp_path / ".sico").exists()


@pytest.mark.parametrize("setting", [None, "", ".sico", ".//heng.xia/.sico"])
def test_assistant_menus_resolve_state_and_report_rejections(tmp_path, setting):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    launch = tmp_path / "personal workspace"
    launch.mkdir()
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CAD_", "SICO_", "PYTHON", "LD_", "PROJ_AI_"))}
    environment.update(SICO_HOME=str(ROOT), PWD=str(launch), CAD_TEMP_DIR="",
                       SICO_PYTHON="/software/pkgs/python/3.9.13/bin/python3")
    if setting is not None:
        environment["SICO_TEMP_DIR"] = setting
    bad = setting == ".//heng.xia/.sico"
    code = f'''
load({json.dumps(str(ROOT / 'tools/ai/skill++/AI.ils'))})
changeWorkingDir({json.dumps(str(tmp_path))})
foreach(agent '("codex" "claude")
  result=errset(aiControllerCommand({json.dumps(str(launch))} agent) t)
  printf("ASSISTANT_STATE_RESULT %s %L\\n" agent and(result stringp(car(result)))))
printf("ASSISTANT_STATE_PROBE_DONE\\n")
exit()
'''
    result = subprocess.run([dbaccess], input=code, env=environment, cwd=launch,
                            text=True, capture_output=True, timeout=45)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "ASSISTANT_STATE_PROBE_DONE" in output, output
    for agent in ("codex", "claude"):
        assert f"ASSISTANT_STATE_RESULT {agent} {'nil' if bad else 't'}" in output, output
    if bad:
        assert "SiCo project state was rejected: SiCo state project directory is unavailable:" in output, output
        assert str(launch / "heng.xia") in output
        assert not (launch / ".sico").exists()
    else:
        assert "*Error*" not in output, output
        assert (launch / ".sico/ai").stat().st_mode & 0o777 == 0o700
    assert not (tmp_path / ".sico").exists()
