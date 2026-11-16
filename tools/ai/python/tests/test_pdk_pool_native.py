"""Opt-in real cdns-ipc and protected dbAccess pool acceptance."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from test_pdk_native import AI, SOURCES


@pytest.mark.skipif(os.environ.get("SICO_AI_RUN_PDK_POOL_NATIVE") != "1",
                    reason="opt-in licensed PDK pool benchmark")
def test_live_capture_equals_all_pool_sizes(tmp_path):
    root = Path(os.environ.get("SICO_AI_PDK_POOL_EVIDENCE", tmp_path))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    assert os.environ.get("SICO_PDK_WORKER_CONTEXT")
    assert os.environ.get("SICO_AI_PDK_TEST_LIBRARY")
    cds = os.environ.get("SICO_AI_PDK_TEST_CDS_LIB")
    if cds:
        (root / "cds.lib").write_text("INCLUDE " + cds + "\n")
    binary = shutil.which("virtuoso")
    assert binary
    command = shlex.join(["/usr/bin/env", "-u", "LD_LIBRARY_PATH", "-u", "PYTHONHOME",
                         sys.executable, str(Path(__file__).with_name("pdk_pool_native_worker.py")), str(root)])
    script = "\n".join(f"load({json.dumps(str(AI / 'skill' / name))})"
                       for name in [*SOURCES, "AI_eval.il", "AI_ipc.il"])
    script += '\n' + (Path(__file__).with_name("pdk_pool_native_probe.il")).read_text()
    script += f'''
aiIpcStart({json.dumps(command)} {json.dumps(str(root))})
pdkPoolTestTicks=0
procedure(pdkPoolTestExit()
  pdkPoolTestTicks=pdkPoolTestTicks+1
  if(or(isFile({json.dumps(str(root / 'finished'))}) pdkPoolTestTicks>4500)
    then exit() else hiRegTimer("pdkPoolTestExit()" 2)))
hiRegTimer("pdkPoolTestExit()" 2)
'''
    startup = root / "start.il"
    startup.write_text(script)
    environment = dict(os.environ, PYTHONPATH=str(AI / "python"))
    with (root / "process.log").open("w") as stream:
        result = subprocess.run([binary, "-nograph", "-nocdsinit", "-norestore",
            "-log", str(root / "CDS.log"), "-restore", str(startup)],
            stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            cwd=root, env=environment, timeout=960)
    assert result.returncode == 0 and (root / "success").is_file(), (root / "process.log").read_text()[-8000:]
    report = json.loads((root / "report.json").read_text())
    assert report["passed"] and len(report["runs"]) == 6
