"""Full real Virtuoso -> IPC -> controller -> authenticated socket -> MCP chain."""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_pdk_native import AI, SOURCES


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_PDK_IPC") != "1", reason="opt-in licensed cdns-ipc acceptance")
def test_pdk_tools_use_the_launching_virtuoso_session(tmp_path):
    binary = shutil.which("virtuoso")
    if not binary:
        pytest.skip("Virtuoso unavailable")
    cds = os.environ.get("SICO_AI_PDK_TEST_CDS_LIB")
    if cds:
        (tmp_path / "cds.lib").write_text("INCLUDE " + cds + "\n")
    command = shlex.join([sys.executable, str(Path(__file__).with_name("pdk_ipc_worker.py")), str(tmp_path)])
    script = "\n".join(f"load({json.dumps(str(AI / 'skill' / name))})"
                       for name in [*SOURCES, "AI_eval.il", "AI_ipc.il"])
    script += f'''
printf("PDK_LAUNCH_PID %d\\n" ipcGetPid())
aiIpcStart({json.dumps(command)} {json.dumps(str(tmp_path))})
pdkTestTicks=0
procedure(pdkTestExit()
 pdkTestTicks=pdkTestTicks+1
 if(or(isFile({json.dumps(str(tmp_path / 'finished'))}) pdkTestTicks>600)
 then exit() else hiRegTimer("pdkTestExit()" 2)))
hiRegTimer("pdkTestExit()" 2)
'''
    startup = tmp_path / "start.il"
    startup.write_text(script)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(AI / "python")
    result = subprocess.run([binary, "-nograph", "-nocdsinit", "-norestore", "-log", str(tmp_path / "CDS.log"),
                             "-restore", str(startup)], stdin=subprocess.DEVNULL, text=True,
                            capture_output=True, cwd=tmp_path, env=environment, timeout=120)
    output = result.stdout + result.stderr
    (tmp_path / "process.log").write_text(output)
    assert result.returncode == 0 and (tmp_path / "success.json").is_file(), output
    success = json.loads((tmp_path / "success.json").read_text())
    assert f"PDK_LAUNCH_PID {success['pid']}" in output
    assert "PARSER WARNING" not in output and "*Error*" not in output, output
