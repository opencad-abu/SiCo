from __future__ import annotations

from skill_integration_fixtures import CAD_ROOT
from skill_test_support import read_skill_source


def test_lef_form_lifecycle_reuses_valid_forms_and_never_deletes_in_callbacks() -> None:
    callback = (CAD_ROOT / "lef/skill++/LEFCB.ils").read_text(encoding="utf-8")
    config = (CAD_ROOT / "lef/skill++/LEFCFG.ils").read_text(encoding="utf-8")
    gui = (CAD_ROOT / "lef/skill++/LEFGUI.ils").read_text(encoding="utf-8")
    helper = read_skill_source(CAD_ROOT / "common/skill/SICO_lsf.il")
    monitor = (CAD_ROOT / "common/skill/SICO_lsfMonitor.il").read_text(
        encoding="utf-8"
    )

    assert "procedure(lefFormDoneCB(form)" in callback
    assert "procedure(lefRunCB(form)" in callback
    assert "procedure(lefRunCloseCB(form)" in callback
    assert "when(lefStart(form)" in callback
    assert "hiDeleteForm" not in callback + config + gui
    assert "?callback 'lefFormDoneCB" in gui
    assert "list('Run\\+Close 'lefRunCloseCB)" in gui
    assert "list('Run 'lefRunCB)" in gui
    assert "list('Close 'hiFormClose)" in gui
    assert "and(boundp('lefForm) lefForm hiIsForm(lefForm))" in gui
    reuse = gui.split("if(and(boundp('lefForm)", 1)[1].split("else", 1)[0]
    assert "SICO_lsfChoicesCB(lefForm)" in reuse
    assert "hiDisplayForm(lefForm)" in reuse
    assert "procedure(SICO_lsfFormValidP(form)" in helper
    assert "SICO_lsfFormValidP(form)" in monitor



def test_lef_frontend_exposes_collapsed_bin_options_and_toml_loader() -> None:
    loader = (CAD_ROOT / "lef/skill++/LEFLOAD.ils").read_text(encoding="utf-8")
    options = (CAD_ROOT / "lef/skill++/LEFOPT.ils").read_text(encoding="utf-8")
    callback = (CAD_ROOT / "lef/skill++/LEFCB.ils").read_text(encoding="utf-8")
    gui = (CAD_ROOT / "lef/skill++/LEFGUI.ils").read_text(encoding="utf-8")

    assert "hiCreateDisclosureTriangle" in gui
    assert '"More Abstract Options"' in gui
    assert "?onFields '(lefBinOptionsLay)" in gui
    assert 'SICO_profileLoadCB(form "LEF")' in loader
    assert "lefApplyFormData" in loader
    assert '"ExtractConnectivity"' in options
    assert '"AbstractBlockageTable"' in options
    assert 'SICO_tomlWriteSection(outFile "abstract.bin_options")' in options
    assert "form~>exportGeometry~>value" in callback



def test_lef_more_options_use_aligned_grid_prompts() -> None:
    options = (CAD_ROOT / "lef/skill++/LEFOPT.ils").read_text(encoding="utf-8")

    assert "hiCreateGridLayout(name" in options
    assert "?labelText lefOptionPrompt(spec)" in options
    assert "?justification 'right" in options
    assert "list('col_stretch 1 1)" in options
    assert "?prompt prompt" not in options



def test_lef_completion_summary_exposes_output_and_log_actions() -> None:
    loader = (CAD_ROOT / "lef/skill++/LEF.ils").read_text(encoding="utf-8")
    runner = (CAD_ROOT / "lef/skill++/LEFRUN.ils").read_text(encoding="utf-8")
    summary = (CAD_ROOT / "lef/skill/UI_lefSummary.il").read_text(encoding="utf-8")

    assert '"/skill/UI_lefSummary.il"' in loader
    assert 'lefSummaryUiRevision()=="20260901.summary.lifecycle.v2"' in loader
    assert "isCallable('GUI_flowLogShow)" in loader
    assert "isCallable('GUI_flowLogClose)" in loader
    assert '"Open LEF File"' in summary
    assert '"Copy LEF File"' in summary
    assert '"Open Log"' in summary
    assert "procedure(lefSummaryOpenCB" in summary
    assert "procedure(lefSummaryCopyCB" in summary
    assert "procedure(lefSummaryOpenLogCB" in summary
    assert "procedure(lefSummaryDisplay" in summary
    assert "procedure(lefSummaryDisposeForm" in summary
    assert summary.index("hiFormClose(form)") < summary.index("hiDeleteForm(form)")
    assert "lefSummaryDisplay(resultMeta exitStatus)" in runner
    assert "lefSummaryCaptureRun(" in runner
