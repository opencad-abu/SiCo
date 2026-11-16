from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from cadai.workflow_tools import build_workflow_skill


CAD_ROOT = Path(__file__).resolve().parents[3]


def test_export_cdl_generate_and_repeated_run_keep_config(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    run_dir = tmp_path / "run with spaces"
    run_dir.mkdir()
    config = run_dir / "export_cdl.toml"
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE reuse /tmp/reuse\n", encoding="utf-8")
    config_text = (
        f'[run]\nrun_dir = "."\ncds_lib = {json.dumps(str(cds_lib))}\n'
        '[input]\ntype = "OA"\n'
        '[input.schematic]\nlib = "reuse"\ncell = "top"\nview = "schematic"\n'
        '[input.layout]\nlib = "reuse"\ncell = "top"\nview = "layout"\n'
    )
    config.write_text(config_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    si = bin_dir / "si"
    si.write_text(
        '#!/bin/sh\nset -eu\n[ "$#" -eq 1 ]\n[ "$1" = "-batch" ]\n'
        "printf '.SUBCKT top A Z\\n.ENDS top\\n' > top.cdl\n",
        encoding="ascii",
    )
    si.chmod(0o755)
    results = tmp_path / "results.jsonl"
    lines = [
        *[f"load({json.dumps(str(CAD_ROOT / 'common/skill' / name))})"
          for name in ("SICO_environment.il", "SICO_installation.il", "SICO_state.il",
                       "SICO_commands.il")],
        f"load({json.dumps(str(CAD_ROOT / 'ai/skill/AI_flow_command.il'))})",
        f"load({json.dumps(str(CAD_ROOT / 'ai/skill/AI_flows.il'))})",
        f'setShellEnvVar("PATH" {json.dumps(str(bin_dir) + ":/usr/bin:/bin")})',
        f"reuseResults=outfile({json.dumps(str(results))} \"w\")",
    ]
    for mode in ("generate", "run", "run"):
        call = build_workflow_skill("export_cdl", {"config_path": str(config), "mode": mode})
        lines += [
            f"reuseStart={call}",
            'fprintf(reuseResults "%s\\n" reuseStart)',
            f"reuseId=aiWorkflowRunning[{json.dumps('cdl|' + str(config))}]",
            'unless(reuseId error("CDL_REUSE: no job was started"))',
            "reuseJob=aiWorkflowJobs[reuseId]",
            "ipcWait(reuseJob['cid] 1 10)",
            'fprintf(reuseResults "%s\\n" aiWorkflowStatus(reuseId))',
        ]
    lines += ["close(reuseResults)", 'printf("CDL_REUSE_OK\\n")']
    replay = "if(errset(progn(\n" + "\n".join(lines) + "\n) t) then exit(0) else exit(1))\n"
    env = {
        **os.environ,
        "CAD_HOME": str(install),
        "LVS_PYTHON": sys.executable,
        "LVS_PYTHON_ENTRY": str(CAD_ROOT / "lvs/python/lvs"),
        "CAD_SYSTEM_LD_LIBRARY_PATH": "",
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "PWD": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("LVS_BACKUP_DONE", None)
    completed = subprocess.run(
        [dbaccess], input=replay, text=True, capture_output=True,
        cwd=tmp_path, env=env, timeout=45, check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0 and "CDL_REUSE_OK" in output, output
    assert "*Error*" not in output, output
    records = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 6, records
    for start, status in zip(records[::2], records[1::2]):
        assert start["ok"] and status["ok"], records
        assert status["state"] == "completed" and status["exit_status"] == 0, records
        assert status["path"] == str(config)
    assert config.read_text(encoding="utf-8") == config_text
    assert (run_dir / "log/export_cdl.toml").read_text(encoding="utf-8") == config_text
    assert (run_dir / "top.cdl").read_text(encoding="ascii") == ".SUBCKT top A Z\n.ENDS top\n"
    assert list(run_dir.glob("top.cdl.*"))
