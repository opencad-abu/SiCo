"""Real SKILL command selection for relocated source and native installations."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[4]
COMMANDS = {"drc": "drc", "lvs": "lvs", "rce": "rce", "lefgen": "lef",
            "sico-lsf": "common", "sico-batch": "common", "sico-profile": "common",
            "nl2view": "common", "mts-netlistor": "mtsnl"}


def stage_installation(tmp_path):
    stage = tmp_path / "installed SiCo's tools"
    for name in ("bin", "tools/common", "etc/config"):
        (stage / name).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "etc/config/sico-install.json", stage / "etc/config")
    return stage


def run_skill(code, stage, work):
    dbaccess = shutil.which("dbAccess")
    if not dbaccess:
        pytest.skip("dbAccess unavailable")
    loads = "\n".join(f'load({json.dumps(str(ROOT / "tools/common/skill" / name))})'
                      for name in ("SICO_toml.il",))
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("CAD_", "SICO_", "PYTHON", "LD_"))}
    environment.update(SICO_HOME=str(stage), SICO_PYTHON=sys.executable, PWD=str(work))
    result = subprocess.run([dbaccess], input=loads + "\n" + code + '\nexit()\n',
                            cwd=work, env=environment, text=True, capture_output=True, timeout=40)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and "SICO_COMMAND_OK" in output, output
    assert "*Error*" not in output, output
    return environment


@pytest.mark.parametrize("native", [False, True])
def test_common_commands_execute_the_selected_entry(tmp_path, native):
    stage = stage_installation(tmp_path)
    if native:
        (stage / "tools/common/context").mkdir()
    code = []
    for command, tool in COMMANDS.items():
        directory = stage / "tools" / tool
        entry = directory / "bin" / command
        entry.parent.mkdir(parents=True, exist_ok=True)
        source = directory / "python" / command
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('import sys\nprint(" ".join(sys.argv[1:]))\n')
        if native:
            shutil.copy2("/bin/echo", entry)
        else:
            entry.write_text("#!/bin/sh\nexit 99\n")
            entry.chmod(0o755)
        output = tmp_path / (command + ".command")
        code += [f'port=outfile({json.dumps(str(output))})',
                 f'fprintf(port "%s" SICO_backendCommand({json.dumps(command)} '
                 f'{json.dumps(sys.executable)} {json.dumps(str(source))}))', 'close(port)']
    code += ['printf("SICO_COMMAND_OK\\n")']
    environment = run_skill("\n".join(code), stage, tmp_path)
    for command, tool in COMMANDS.items():
        invocation = (tmp_path / (command + ".command")).read_text()
        tokens = shlex.split(invocation)
        expected = stage / "tools" / tool / ("bin" if native else "python") / command
        assert str(expected) in tokens
        assert ("-s" in tokens) is not native
        result = subprocess.run(tokens + ["argument with spaces", "$(literal)", "a'b"],
                                env=environment, text=True, capture_output=True, check=True)
        assert result.stdout.strip() == "argument with spaces $(literal) a'b"


@pytest.mark.parametrize("defect", ["missing", "script", "escape"])
@pytest.mark.parametrize("context_tool", ["common", "ai", "drc"])
def test_runtime_commands_reject_source_fallback_and_escaping_entries(tmp_path, defect, context_tool):
    stage = stage_installation(tmp_path)
    (stage / "tools" / context_tool / "context").mkdir(parents=True)
    entry = stage / "tools/drc/bin/drc"
    entry.parent.mkdir(parents=True)
    if defect == "script":
        entry.write_text("#!/bin/sh\nexit 0\n")
        entry.chmod(0o755)
    elif defect == "escape":
        entry.symlink_to("/bin/true")
    run_skill('''
when(errset(SICO_nativeEntry("drc") nil) error("invalid runtime entry admitted"))
when(errset(SICO_nativeEntry("../other") nil) error("unknown command admitted"))
printf("SICO_COMMAND_OK\\n")
''', stage, tmp_path)


def test_development_tree_keeps_source_entries_beside_worker_contexts(tmp_path):
    # The development bootstrap marks a tree that runs its tool sources; worker
    # contexts installed there are test dependencies, not a compiled runtime.
    stage = stage_installation(tmp_path)
    (stage / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/sicoAutoLoad.il", stage / "scripts/sicoAutoLoad.il")
    for context in ("tools/context", "tools/ai/context"):
        (stage / context).mkdir(parents=True)
    entry = stage / "tools/mtsnl/bin/mts-netlistor"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    entry.chmod(0o755)
    run_skill('''
native=SICO_nativeEntry("mts-netlistor")
when(native error("development source launcher reported a native entry"))
shared=SICO_nativeEntry("sico-lsf")
when(shared error("development source command reported a native entry"))
printf("SICO_COMMAND_OK\\n")
''', stage, tmp_path)
    # A compiled entry in the same development tree stays authoritative.
    shutil.copy2("/bin/echo", entry)
    run_skill(f'''
unless(SICO_nativeEntry("mts-netlistor")=={json.dumps(str(entry))}
  error("compiled entry was not selected"))
printf("SICO_COMMAND_OK\\n")
''', stage, tmp_path)


def test_common_environment_preserves_explicit_new_selection(tmp_path):
    stage = stage_installation(tmp_path)
    run_skill('''
setShellEnvVar("CAD_LSF_PYTHON_ENTRY" "/legacy/entry")
setShellEnvVar("SICO_LSF_PYTHON_ENTRY" "")
when(errset(SICO_envValue("SICO_LSF_PYTHON_ENTRY") nil) error("empty entry fell back"))
setShellEnvVar("CAD_PYTHON" "/legacy/python")
unless(SICO_envValue("SICO_PYTHON")==getShellEnvVar("SICO_PYTHON") error("Python precedence"))
setShellEnvVar("CAD_SYSTEM_LD_LIBRARY_PATH" "/legacy/library")
setShellEnvVar("SICO_SYSTEM_LD_LIBRARY_PATH" "")
unless(SICO_pythonLibraryPath()=="" error("empty library path fell back"))
printf("SICO_COMMAND_OK\\n")
''', stage, tmp_path)


def test_ai_flow_uses_the_same_native_entry_and_new_environment(tmp_path):
    stage = stage_installation(tmp_path)
    (stage / "tools/common/context").mkdir()
    for name in ("drc", "lvs", "rce", "lefgen"):
        entry = stage / "tools" / COMMANDS[name] / "bin" / name
        entry.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2("/bin/echo", entry)
    code = '\n'.join(f'load({json.dumps(str(ROOT / "tools/ai/skill" / name))})'
                     for name in ("AI_flow_command.il", "AI_flows.il"))
    code += '''
procedure(aiWorkflowTempEnvironment() "")
setShellEnvVar("CAD_PYTHON" "/missing/legacy")
setShellEnvVar("SICO_SYSTEM_LD_LIBRARY_PATH" "/configured/library")
'''
    for flow, command, subcommand in (("drc", "drc", "run"), ("lvs", "lvs", "run"),
                                      ("gds", "lvs", "stream-gds"), ("cdl", "lvs", "export-cdl"),
                                      ("rce", "rce", "run"), ("lef", "lefgen", "run")):
        output = tmp_path / (flow + ".command")
        code += f'''
unless(aiWorkflowEntry("{flow}" aiWorkflowToolsRoot())==SICO_nativeEntry("{command}")
  error("flow selection differs"))
port=outfile({json.dumps(str(output))})
fprintf(port "%s" aiWorkflowCommand("{flow}" "/input with spaces.toml" "run" nil nil
  aiWorkflowPython("{flow}") aiWorkflowEntry("{flow}" aiWorkflowToolsRoot())))
close(port)
'''
    code += '''
setShellEnvVar("SICO_PYTHON" "")
when(aiWorkflowExecutableP(aiWorkflowPython("drc")) error("empty Python fell back"))
printf("SICO_COMMAND_OK\\n")
'''
    environment = run_skill(code, stage, tmp_path)
    for flow in ("drc", "lvs", "gds", "cdl", "rce", "lef"):
        invocation = (tmp_path / (flow + ".command")).read_text()
        assert "SICO_PYTHON=" in invocation and "LD_LIBRARY_PATH='/configured/library'" in invocation
        result = subprocess.run(invocation, shell=True, env=environment,
                                text=True, capture_output=True, check=True)
        assert result.stdout.strip() == {"gds": "stream-gds", "cdl": "export-cdl"}.get(flow, "run") + " /input with spaces.toml"
