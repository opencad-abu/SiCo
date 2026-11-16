"""Synthetic Calibre flow fixtures for DataHub source regressions."""

from __future__ import annotations

import shlex
import time
from types import SimpleNamespace

from sico_datahub.calibre.runner import CalibreRunner


def write_script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + body, encoding="utf-8")
    path.chmod(0o700)
    return path


def make_flow(tmp_path, *, kind="drc", selected=False):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    gds = tmp_path / "input.gds"
    gds.write_text("GDS\n", encoding="ascii")
    cdl = tmp_path / "input.cdl"
    cdl.write_text("CDL\n", encoding="ascii")
    runset = tmp_path / (kind + ".cal")
    runset_text = 'LAYER M1\nGROUP G1 RULE_A\nRULE_A { LAYER M1 }\n'
    runset.write_text(runset_text, encoding="utf-8")
    selection = ""
    if selected:
        selection = (
            'rule_select_enable = true\n'
            'rule_select_groups = ["G1"]\n'
        )
    config = tmp_path / (kind + ".toml")
    config.write_text(
        f'[run]\nrun_dir = "{run_dir}"\n'
        f'[input]\ntype = "CDL+GDS"\n'
        f'[input.cdl]\ncell = "inv"\nfile = "{cdl}"\n'
        f'[input.gds]\ncell = "inv"\nfile = "{gds}"\n'
        f'[{kind}]\nrunset_file = "{runset}"\nrun_mode = "Flat"\n'
        + selection,
        encoding="utf-8",
    )
    return SimpleNamespace(
        kind=kind,
        config=config,
        run_dir=run_dir,
        log_dir=run_dir / "log",
        db_dir=run_dir / "db",
        runset=runset,
        gds=gds,
    )


def make_calibre(tmp_path):
    executable = tmp_path / "calibre/bin/calibre"
    write_script(
        executable,
        'if [ "$1" = "-version" ]; then\n'
        '  printf "Calibre 2026.3 fixture\\n"\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n',
    )
    return executable.parent.parent


def drc_script(run_dir, *, summary="", results="", exit_code=0, delay=0):
    log_dir = run_dir / "log"
    result_file = run_dir / "db/drc/cal_drc.out"
    body = (
        f"sleep {delay}\n" if delay else ""
    ) + (
        f"mkdir -p {shlex.quote(str(log_dir))} {shlex.quote(str(result_file.parent))}\n"
        f"printf %s {shlex.quote(summary)} > {shlex.quote(str(log_dir / 'cal_drc.sum'))}\n"
        f"printf %s {shlex.quote(results)} > {shlex.quote(str(result_file))}\n"
        f"exit {exit_code}\n"
    )
    return body


def lvs_script(run_dir, *, report="", svdb=True, exit_code=0, delay=0):
    log_dir = run_dir / "log"
    svdb_dir = run_dir / "db/svdb.inv"
    body = f"sleep {delay}\n" if delay else ""
    body += (
        f"mkdir -p {shlex.quote(str(log_dir))}\n"
        f"printf %s {shlex.quote(report)} > {shlex.quote(str(log_dir / 'lvs.report'))}\n"
    )
    if svdb:
        body += (
            f"mkdir -p {shlex.quote(str(svdb_dir))}\n"
            f"printf 'svdb\\n' > {shlex.quote(str(svdb_dir / 'data.db'))}\n"
        )
    body += f"exit {exit_code}\n"
    return body


def make_runner(tmp_path, *, drc_body="exit 0\n", lvs_body="exit 0\n", timeout=10):
    root = make_calibre(tmp_path)
    drc = write_script(tmp_path / "bin/drc", drc_body)
    lvs = write_script(tmp_path / "bin/lvs", lvs_body)
    return CalibreRunner(
        tmp_path,
        calibre_root=root,
        drc_command=drc,
        lvs_command=lvs,
        timeout=timeout,
    )


def wait_terminal(runner, run_ref, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = runner.status(run_ref)
        if receipt.terminal:
            return receipt
        time.sleep(0.02)
    raise AssertionError("Calibre job did not reach a terminal state")


__all__ = [
    "drc_script",
    "lvs_script",
    "make_calibre",
    "make_flow",
    "make_runner",
    "wait_terminal",
    "write_script",
]
