"""Verify per-corner text-view publication using the real Virtuoso frontend."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cadview.artifacts import master_file
from test_absolute_path_skill_probe import _install_tree, _run_probe, _write


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_corner_text_views_preserve_sources_and_single_output_names(
    tmp_path: Path,
) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    _write(launch / "cds.lib", "")
    library = launch / "rce_corner_view_probe"
    dspf = (
        "*|DSPF 1.3\n.SUBCKT top in out\n*|GROUND_NET 0\n"
        "*|NET in 1p\nR1 in out {resistance}\nC1 out 0 1p\n.ENDS top\n"
    )
    sources = {
        corner: _write(
            launch / f"top_{corner}.dspf", dspf.format(resistance=resistance)
        )
        for corner, resistance in (("RCmax", 10), ("Cmin", 2))
    }
    entry = f"{install}/tools/rce/skill++/RCE.ils"
    output = _run_probe(
        tmp_path,
        flow="RCE",
        entry=entry,
        commands=(
            # A running session must reload the changed builder even though the
            # form and unrelated frontend revision numbers remain current.
            "putd('rceNetlistViewRevision lambda(() \"old\"))",
            "putd('rceNetlistViewRequest lambda((form target paths finalPaths kind)",
            '  error("Stale view callback was not reloaded")))',
            f'load("{entry}")',
            'unless(rceNetlistViewRevision()=="20260911.corner.text.views.v1"',
            '  error("View publication revision was not reloaded"))',
            f'probeLib=ddCreateLib("rce_corner_view_probe" "{library}")',
            'probeCv=dbOpenCellViewByType("rce_corner_view_probe" "top"',
            '  "layout" "maskLayout" "w")',
            "when(probeCv dbSave(probeCv) dbClose(probeCv))",
            "cadDisplayRceForm() form=rceForm",
            'form~>inpType~>value="OA"',
            'form~>topCellSource~>value="Layout"',
            'form~>layLib~>value="rce_corner_view_probe"',
            'form~>layCell~>value="top"',
            "form~>createNetlistView~>value=t",
            "form~>reductionEnable~>value=nil",
            'form~>cornerType~>value="Multiple Corners"',
            'rcePopulateMultiCornerRows(form list("RCmax" "RCmin" "Cmin") "RCmax")',
            "nth(2 form~>rceCornerSelectFields)~>value=list(t)",
            'corners=rceSelectedProcessCorners(form)',
            'unless(equal(corners list("RCmax" "Cmin"))',
            '  error("Selected corners were not preserved"))',
            "target=rceNetlistViewTarget(form)",
            'unless(target error("Missing OA target"))',
            'foreach(tool list("QRC" "StarRC" "CalXRC")',
            "  form~>extTool~>value=tool",
            '  foreach(spec list(list("dspf" "dspfText")',
            '                    list("sp" "spiceText") list("spef" "spef"))',
            "    outputType=car(spec) baseView=cadr(spec)",
            "    form~>outType~>value=outputType",
            '    paths=rceExpectedOutputPaths(pwd() "top" outputType tool corners)',
            "    request=rceNetlistViewRequest(form target paths paths outputType)",
            "    unless(equal(request rceBatchViewRequest(",
            "                     form pwd() paths paths outputType))",
            '      error("Batch and single-cell view targets differ"))',
            '    if(and(tool=="QRC" outputType=="sp") then',
            '      unless(and(car(request)=="text" length(request)==6',
            '                 nth(3 request)==strcat(pwd() "/top.sp"))',
            '        error("Vectorized SPICE must keep its single view"))',
            "    else",
            '      unless(and(car(request)=="group" length(request)==3)',
            '        error("Missing per-corner view requests: %L" request))',
            "      first=cadr(request) second=caddr(request)",
            '      unless(and(car(first)=="text" car(second)=="text"',
            "                 nth(3 first)==car(paths) nth(3 second)==cadr(paths)",
            '                 nth(6 first)==strcat(baseView "_RCmax")',
            '                 nth(6 second)==strcat(baseView "_Cmin"))',
            '        error("Corner names and source files do not match: %L" request))',
            "      unless(rceSummaryViewTargetText(request)==strcat(",
            '        "rce_corner_view_probe/top/" baseView "_RCmax, "',
            '        "rce_corner_view_probe/top/" baseView "_Cmin")',
            '        error("Summary does not show both corner views"))',
            "      unless(equal(rceBatchPrimaryViewRequest(request) second)",
            '        error("Batch result lost the explicit corner view"))',
            '      when(and(tool=="QRC" outputType=="dspf") savedRequest=request)',
            "    )",
            "  )",
            ")",
            'form~>outType~>value="dspf"',
            "form~>createNetlistView~>value=nil",
            "when(rceNetlistViewRequest(form target paths paths \"dspf\")",
            '  error("Disabled view creation produced requests"))',
            "form~>createNetlistView~>value=t",
            "when(rceNetlistViewRequest(form nil paths paths \"dspf\")",
            '  error("Missing OA target produced requests"))',
            # Changing the form after Run must not retarget pending imports.
            "nth(2 form~>rceCornerSelectFields)~>value=list(nil)",
            'form~>corner~>value="RCmax"',
            'foreach(mode list("Multiple Corners" "Single Corner")',
            "  form~>cornerType~>value=mode",
            '  paths=rceExpectedOutputPaths(pwd() "top" "dspf" "QRC"',
            "    rceSelectedProcessCorners(form))",
            '  request=rceNetlistViewRequest(form target paths paths "dspf")',
            '  unless(and(car(request)=="text" length(request)==6',
            '             nth(3 request)==strcat(pwd() "/top.dspf")',
            '             rceSummaryViewTargetText(request)==',
            '               "rce_corner_view_probe/top/dspfText")',
            '    error("Single output no longer uses the standard DSPF view"))',
            ")",
            'unless(rceCompleteViewRequest(savedRequest strcat(pwd() "/rce.launch.log"))',
            '  error("Per-corner DSPF import failed"))',
            "ddUpdateLibList()",
            'foreach(view list("dspfText_RCmax" "dspfText_Cmin")',
            '  unless(ddGetObj("rce_corner_view_probe" "top" view)',
            '    error("Corner view missing: %s" view)))',
            'when(ddGetObj("rce_corner_view_probe" "top" "dspfText")',
            '  error("Multi-corner import created an unsuffixed view"))',
            "hiFormClose(form) hiDeleteForm(form)",
        ),
        env_updates={
            "CAD_HOME": str(install),
            "CDS_LIB": "cds.lib",
            "RCE_DB_DIR": str(tmp_path),
            "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf,sp,view,spef",
        },
    )
    for corner, source in sources.items():
        view = library / "top" / f"dspfText_{corner}"
        assert master_file(view).read_bytes() == source.read_bytes()
        assert (view / "netlist.oa").stat().st_size > 0
        assert f"rce_corner_view_probe/top/dspfText_{corner}" in output
        assert (launch / f"rce.launch.log.nl2view.dspfText_{corner}").is_file()
