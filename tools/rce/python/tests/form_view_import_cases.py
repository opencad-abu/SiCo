from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from form_lifecycle_fixtures import RUN_PROBE, _install_tree, _skill_literal
from skill_probe_support import run_virtuoso_source


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the RCE dual-view import probe",
)
def test_rce_imports_original_and_reduced_text_views(tmp_path: Path) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = _install_tree(tmp_path)
    library_path = tmp_path / "rce_dual_view_probe"
    original = tmp_path / "top.sp"
    reduced = tmp_path / "top.reduced.sp"
    original_dspf = tmp_path / "top.dspf"
    reduced_dspf = tmp_path / "top.reduced.dspf"
    original.write_text(
        "* original netlist\n.SUBCKT top in out\nR1 in out 1\n.ENDS top\n",
        encoding="utf-8",
    )
    reduced.write_text(
        "* reduced netlist\n.SUBCKT top in out\nR1 in out 2\n.ENDS top\n",
        encoding="utf-8",
    )
    dspf_text = (
        "*|DSPF 1.3\n.SUBCKT top in out\n*|GROUND_NET 0\n"
        "*|NET in 1p\nR1 in out 1\nC1 out 0 1p\n.ENDS top\n"
    )
    original_dspf.write_text(dspf_text, encoding="utf-8")
    reduced_dspf.write_text(
        dspf_text.replace("R1 in out 1", "R1 in out 2"), encoding="utf-8"
    )
    replay = tmp_path / "rce-dual-view-import.il"
    log = tmp_path / "rce-dual-view-import.log"
    launch_log = tmp_path / "rce.launch.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" {_skill_literal(install)})',
                f'load({_skill_literal(install / "tools/rce/skill++/RCE.ils")})',
                'probeLib=ddCreateLib("rce_dual_view_probe" '
                f'{_skill_literal(library_path)})',
                'probeCv=dbOpenCellViewByType("rce_dual_view_probe" "top" '
                '"layout" "maskLayout" "w")',
                "when(probeCv dbSave(probeCv) dbClose(probeCv))",
                'request=list("group"',
                '  list("text" "rce_dual_view_probe" "top"',
                f'       {_skill_literal(original)} "sp" '
                f'{_skill_literal(tmp_path / "cds.lib")})',
                '  list("text" "rce_dual_view_probe" "top"',
                f'       {_skill_literal(reduced)} "sp" '
                f'{_skill_literal(tmp_path / "cds.lib")} "spiceText_reduced")',
                '  list("text" "rce_dual_view_probe" "top"',
                f'       {_skill_literal(original_dspf)} "dspf" '
                f'{_skill_literal(tmp_path / "cds.lib")})',
                '  list("text" "rce_dual_view_probe" "top"',
                f'       {_skill_literal(reduced_dspf)} "dspf" '
                f'{_skill_literal(tmp_path / "cds.lib")} "dspfText_reduced"))',
                f'importOk=rceCompleteViewRequest(request {_skill_literal(launch_log)})',
                "ddUpdateLibList()",
                'originalView=ddGetObj("rce_dual_view_probe" "top" "spiceText")',
                'reducedView=ddGetObj("rce_dual_view_probe" "top" '
                '"spiceText_reduced")',
                'originalDspfView=ddGetObj("rce_dual_view_probe" "top" '
                '"dspfText")',
                'reducedDspfView=ddGetObj("rce_dual_view_probe" "top" '
                '"dspfText_reduced")',
                "when(and(importOk originalView reducedView",
                "         originalDspfView reducedDspfView)",
                '  printf("RCE_DUAL_TEXT_VIEW_IMPORT_OK\\n"))',
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(replay.read_text(), tmp_path,
        env_updates={'RCE_DB_DIR': str(tmp_path), 'RCE_DEF_TOOL': 'QRC', 'RCE_OUTPUT_CHOICES': 'dspf,sp,view,spef'}, log_path=log)
    if launch_log.is_file():
        output += launch_log.read_text(encoding="utf-8", errors="replace")
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "RCE_DUAL_TEXT_VIEW_IMPORT_OK" in output
