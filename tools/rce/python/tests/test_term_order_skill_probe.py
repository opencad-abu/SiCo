"""Exercise the OA terminal-order preflight and persisted CDF updates in IC23.1."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_absolute_path_skill_probe import _install_tree, _write


CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_term_order_loader_recovers_incomplete_same_version(tmp_path: Path) -> None:
    source = CAD_ROOT / "rce/skill/RCE_termOrder.il"
    commands = f'''
rtLoaderFunctions='(rceTermOrderRevision rceTermOrderSamePins rceTermOrderSnapshot
  rceTermOrderMismatchP rceTermOrderUnchangedP rceTermOrderPrompt
  rceTermOrderRestore rceTermOrderUpdate rceCheckOaTermOrder)
foreach(rtLoaderFunction rtLoaderFunctions putd(rtLoaderFunction nil))
rceTermOrderVersion="20260911.view.term.order.v2"
load("{source}")
unless(forall(rtLoaderFunction rtLoaderFunctions isCallable(rtLoaderFunction))
  error("RCE_TEST: cached revision prevented initial function loading"))
foreach(rtLoaderFunction rtLoaderFunctions
  putd(rtLoaderFunction nil)
  load("{source}")
  unless(isCallable(rtLoaderFunction)
    error("RCE_TEST: missing helper was not restored: %L" rtLoaderFunction)))
rtLoaderOriginal=getd('rceCheckOaTermOrder)
load("{source}")
unless(eq(rtLoaderOriginal getd('rceCheckOaTermOrder))
  error("RCE_TEST: complete same-version helpers were redefined"))
unless(rceCheckOaTermOrder(nil)
  error("RCE_TEST: non-OA input did not pass the preflight"))
printf("RCE_TERM_ORDER_LOADER_OK\\n")
'''
    output = _run(tmp_path / "launch", {}, "loader", commands)
    assert "*Error*" not in output, output
    assert "\\o RCE_TERM_ORDER_LOADER_OK" in output, output


def _run(launch: Path, env: dict[str, str], name: str, commands: str) -> str:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    replay = _write(launch / f"{name}.il", commands + "\nexit()\n")
    log = launch / f"{name}.log"
    with (launch / f"{name}.stdout").open("w+", encoding="utf-8") as stream:
        completed = subprocess.run(
            [virtuoso, "-nograph", "-nocdsinit", "-replay", str(replay), "-log", str(log)],
            cwd=launch,
            env={**os.environ, **env},
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=120,
            check=False,
        )
        stream.seek(0)
        output = stream.read()
    if log.is_file():
        output += log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, output
    assert "*Error* RCE_TEST:" not in output, output
    assert "(reader)" not in output, output
    assert "still unclosed on EOF" not in output, output
    return output


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_oa_term_order_preflight_and_persistence(tmp_path: Path) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    basic = Path(virtuoso).absolute().parents[1] / "etc/cdslib/basic"
    assert basic.is_dir(), basic
    _write(launch / "cds.lib", f"DEFINE basic {basic}\n")
    _write(launch / "tech/Typ/qrcTechFile")
    (launch / "runs").mkdir()
    env = {
        "CAD_HOME": str(install),
        "CDS_LIB": "cds.lib",
        "RCE_DB_DIR": str(launch / "runs"),
        "QUANTUS_TECH_DIR": f"probe,{launch}/tech/Typ",
        "RCE_DEF_TOOL": "QRC",
        "RCE_OUTPUT_CHOICES": "dspf,sp,view,spef",
        "RCE_CORNER": "Typ",
    }
    load = f'load("{install}/tools/rce/skill++/RCE.ils")\n'
    fixture = CAD_ROOT / "rce/python/tests/term_order_fixture.il"
    output = _run(
        launch, env, "behavior", load + f'load("{fixture}")\nrceTermTestMain()'
    )
    assert "\\o RCE_TERM_ORDER_BEHAVIOR_OK" in output, output
    assert "injected post-update verification failure" in output, output
    assert "rollback failed" not in output, output

    # A fresh Virtuoso process verifies persistence instead of only cached CDF.
    output = _run(
        launch,
        env,
        "persistence",
        load
        + "\n".join(
            (
                'foreach(cell list("top" "no_symbol")',
                '  snapshot=rceTermOrderSnapshot("rce_term_probe" cell "schematic")',
                '  when(rceTermOrderMismatchP(snapshot) error("RCE_TEST: persisted orders differ"))',
                '  foreach(tool list("auCdl" "spectre")',
                "    unless(equal(cadr(assoc(tool snapshot['baseOrders])) snapshot['schematic])",
                '      error("RCE_TEST: base CDF order was not persisted")))',
                ")",
                'printf("RCE_TERM_ORDER_PERSISTENCE_OK\\n")',
            )
        ),
    )
    assert "*Error*" not in output, output
    assert "\\o RCE_TERM_ORDER_PERSISTENCE_OK" in output, output
