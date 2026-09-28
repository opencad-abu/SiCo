import re

from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_rce_quantus_reduction_is_wired_across_gui_toml_and_completion() -> None:
    reduction = read_skill_source(CAD_ROOT / "rce/skill++/RCEREDUCTION.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    profile = read_skill_source(CAD_ROOT / "rce/skill++/RCEPROFILE.ils")
    batch = read_skill_source(CAD_ROOT / "rce/skill++/RCEBATCH.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    # Native-view destination controls come first; the user chooses the output
    # target before opting into standalone reduction.  The subordinate
    # reduction layout starts hidden.
    out_start = gui.index("defmethod(makeOutLay")
    out_end = gui.index(");defmethod makeOutLay", out_start)
    out_layout = gui[out_start:out_end]
    assert out_layout.index("inst->nativeViewLay") < out_layout.index(
        "inst->reductionEnableLay"
    )
    # View Target is a child of nativeViewLay; the reduction switch follows
    # that complete native-view destination block in the parent layout.
    assert "list(inst->viewKind inst->viewKindFixed inst->viewName inst->viewTarget" in gui
    assert out_layout.index("inst->createNetlistView") < out_layout.index(
        "inst->reductionEnable"
    ) < out_layout.index("inst->reductionOptions")
    assert (
        '?buttonText     "Run Standalone Reduction After Extraction"'
        in reduction
    )
    assert "inst->reductionEnableLay=makehbl(" in reduction
    assert "list(inst->reductionEnable list('stretch_item 1))" in reduction
    assert "?a left" in reduction
    assert 'hiCreateGridLayout(' in reduction
    assert 'hiCreateLabel(' in reduction
    assert '?frame "Reduction Options"' in reduction
    assert "?justification 'right" in reduction
    assert "'col_min_width 0 190" in reduction
    assert "'col_min_width 1 180" in reduction
    assert "'col_stretch 1 1" in reduction
    assert "'reductionDelayLay" not in reduction
    assert "hiSetFieldMinSize(form 'reductionDelayRel ?widgetWidth 180)" in reduction
    assert "hiSetFieldMinSize(form 'reductionDelayAbs ?widgetWidth 180)" in reduction
    assert "hiSetFieldMinSize(form 'reductionFrequency ?widgetWidth 180)" in reduction
    assert "rceAlignReductionPrompts(form)" in reduction
    assert "rceAlignReductionPrompts(gui->mainForm)" in gui
    sync_reduction = _procedure_body(reduction, "rceSyncReduction")
    mode_callback = _procedure_body(reduction, "rceReductionModeCB")
    assert "rceAlignReductionPrompts(form)" not in sync_reduction
    assert "rceSyncReduction(form)" in mode_callback
    assert "createNetlistView" not in reduction
    assert "procedure(rceReductionMultiCornerP(form)" in reduction
    assert "!rceReductionMultiCornerP(form)" in reduction
    assert "form~>reductionEnable~>value=nil" in reduction
    assert "Reduction is disabled for Multiple Corners." in reduction
    assert re.search(r"[?]defValue\s+nil", reduction)
    assert "inst->reductionOptions->invisible=t" in reduction
    assert "form~>reductionOptions~>invisible=!enabled" in reduction
    assert 'mode=="Reduction Control"' in reduction
    assert 'mode=="Delay and Frequency"' in reduction
    assert 'mode=="Selection File"' in reduction
    assert "rceReductionTemperatureSupportedP(form)" in reduction
    assert "spiceOutput" in reduction
    assert 'form~>reductionTemperature~>value=""' in reduction
    assert 'form~>reductionCanonicalDeviceFile~>value=""' in reduction

    toml_fields = (
        "enabled",
        "mode",
        "output_tag",
        "cpus",
        "control",
        "delay_rel",
        "delay_abs",
        "frequency",
        "temperature",
        "ground",
        "reduce_negative",
        "selection_file",
        "canonical_device_file",
    )
    assert 'SICO_tomlWriteSection(outFile "reduction")' in callback
    assert 'SICO_tomlWriteString(outFile "corner_scope"' in callback
    corner_callback = _procedure_body(callback, "rceCornerTypeCB")
    assert "rceSyncReduction(form)" in corner_callback
    for field in toml_fields:
        assert re.search(
            rf'SICO_tomlWrite(?:Bool|String)\(outFile "{field}"', callback
        ), field

    profile_fields = tuple(field for field in toml_fields if field != "cpus")
    for field in profile_fields:
        assert f'("reduction.{field}"' in profile
    assert "rceSyncReduction(form)" in profile

    # The summary continues to point at the final reduced file, while text-view
    # publication keeps both the extractor's original result and qreduce output.
    single_start = _procedure_body(callback, "rceStartReserved")
    assert "finalOutputPaths=rceFinalOutputPaths(form outputPaths outputType)" in single_start
    assert single_start.index(
        "finalOutputPaths=rceFinalOutputPaths(form outputPaths outputType)"
    ) < single_start.index("viewRequest=rceNetlistViewRequest(")
    assert "form viewTarget outputPaths finalOutputPaths outputType" in single_start
    assert (
        "rceSummaryCaptureRun(form runPathPrefix finalOutputPaths outputType"
        in callback
    )
    assert "viewTarget=rceReductionViewTarget(form rceNativeViewTarget(form))" in callback
    view_builder = _procedure_body(callback, "rceNetlistViewRequest")
    assert re.search(
        r'originalRequest=rceTextViewRequest\(\s*viewTarget car\(outputPaths\)',
        view_builder,
    )
    assert "reducedPath=car(finalOutputPaths)" in view_builder
    assert "reducedView=rceReducedParasiticViewName(" in view_builder
    assert 'list("group" originalRequest reducedRequest)' in view_builder
    completion = _procedure_body(callback, "rceCompleteViewRequest")
    assert '("group"' in completion
    assert "rceCompleteViewRequest(childRequest launchLog)" in completion
    assert "allCreated=nil" in completion
    batch_spec = _procedure_body(batch, "rceBatchSpec")
    assert "finalOutputPaths=rceFinalOutputPaths(form outputPaths outputType)" in batch_spec
    assert 'outputPath=or(car(finalOutputPaths) "")' in batch_spec
    assert batch_spec.index(
        "finalOutputPaths=rceFinalOutputPaths(form outputPaths outputType)"
    ) < batch_spec.index("viewRequest=rceBatchViewRequest(")
    assert "form runDir outputPaths finalOutputPaths outputType" in batch_spec
    assert "cadBatchSpecSet(spec 'outputPaths finalOutputPaths)" in batch_spec
    assert "viewTarget=rceReductionViewTarget(form rceNativeViewTarget(form))" in batch
    batch_view = _procedure_body(batch, "rceBatchViewRequest")
    assert "request=rceNetlistViewRequest(" in batch_view
    assert "form viewTarget outputPaths finalOutputPaths outputType" in batch_view
    assert "request=rceBatchPrimaryViewRequest(spec['viewRequest])" in batch
    assert "requestedViewName=nth(6 request)" in batch

    # Generate View alone publishes the original file to the standard text view.
    # Generate View plus Reduction also publishes a postfix-named reduced view.
    parasitic_spec = _procedure_body(callback, "rceParasiticViewSpec")
    assert '("dspf" list("dspfText" "DSPF"))' in parasitic_spec
    assert '(("sp" "spice") list("spiceText" "Spice"))' in parasitic_spec
    reduced_view = _procedure_body(callback, "rceReducedParasiticViewName")
    assert 'strcat(car(viewSpec) "_" tag)' in reduced_view
    importer = _procedure_body(callback, "rceCreateNetlistViewFromRequest")
    assert "requestedViewName=nth(5 request)" in importer
    assert 'then strcat("." viewName) else ""' in importer
    assert '" --view " SICO_shellQuote(viewName)' in importer
    assert 'cond(\n      (member(lowerCase(or(outputType ""))' in importer
    assert 'cond(\n      ((member(lowerCase(or(outputType ""))' not in importer
    assert 'strcat(caddr(target) "_" form~>reductionOutputTag~>value)' in reduction

    assert 'strcat(rceSkillRoot "/skill++/RCEREDUCTION.ils")' in entry
    assert 'rceReductionRevision()=="20260908.recognize.gates.v1"' in entry
    assert 'rceLoaderRevision()=="20260924.flow.environment.v3"' in entry
