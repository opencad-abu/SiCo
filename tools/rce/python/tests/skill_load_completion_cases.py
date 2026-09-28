from skill_load_fixtures import (
    CAD_ROOT,
    _procedure_body,
)
from skill_test_support import read_skill_source


def test_flow_log_widget_tails_the_complete_launch_log() -> None:
    text = read_skill_source(CAD_ROOT / "utility/skill/UI_flowLog.il")

    assert "boundp('sicoFlowLogVersion)" in text
    assert 'sicoFlowLogVersion=="20260721.flow.log.owned.v2"' in text
    assert "procedure(GUI_flowLogRevision()" in text
    assert "procedure(GUI_flowLogOpen(flow logFile)" in text
    assert "procedure(GUI_flowLogShow(requestId)" in text
    assert "procedure(GUI_flowLogClose(requestId)" in text
    assert "cadFlowLogWindows[requestId]=win" in text
    assert "cadFlowLogWindowRequests[win]=requestId" in text
    assert "hiRegCloseProc(win 'GUI_flowLogWindowClosed)" in text
    assert "hiViewTextFile(logFile" in text
    assert '?appName strcat(GUI_flowLogBrand() "FlowLog")' in text
    assert "hiEnableTailViewfile(win)" in text
    assert "hiScrollWindowBottom(win)" in text
    assert "hiRegTimer(" in text
    assert "attempts<50" in text
    assert "cadFlowLogLastWindow" not in text
    assert "hiCreateMLTextField" not in text


def test_standard_forms_keep_their_shell_and_flow_logs_use_the_shared_logo() -> None:
    logo = CAD_ROOT.parent / "share/sico/icons/brand/logo.png"
    helper = read_skill_source(CAD_ROOT / "utility/skill/UI_windowIcon.il")
    flow_log = read_skill_source(CAD_ROOT / "utility/skill/UI_flowLog.il")

    assert logo.is_file()
    assert logo.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert 'SICO_iconPath(SICO_installationRoot() "brand" "logo.png")' in helper
    assert "procedure(SICO_windowIconRevision()" in helper
    assert "hiLoadIconFile(path 48 48)" in helper
    assert "hiOpenWindow(" not in helper
    assert '?type "form"' not in helper
    assert "form~>cadWindow" not in helper
    assert "windowp(window)" in helper
    assert "hiSetWindowIcon(window icon)" in helper
    assert "CAD_closeForm" not in helper
    assert "CAD_displayForm" not in helper
    assert "hiUnmapWindow" not in helper
    assert "hiMapWindow" not in helper
    assert "hiCloseWindow" not in helper
    assert "_bannerID" not in helper
    assert "SICO_setWindowIcon(win)" in flow_log

    display_sources = (
        "drc/skill++/DRCGUI.ils",
        "lvs/skill++/LVSGUI.ils",
        "lvs/skill++/STAGEGUI.ils",
        "rce/skill++/RCEGUI.ils",
        "rce/skill/UI_rceSelectCellForm.il",
        "rce/skill/UI_rceSummary.il",
    )
    for relative in display_sources:
        text = read_skill_source(CAD_ROOT / relative)
        assert "hiDisplayForm" in text
        assert "CAD_displayForm" not in text

    for relative, callback in (
        ("rce/skill++/RCE.ils", '"/skill++/RCECB.ils"'),
        ("lvs/skill++/LVS.ils", '"/skill++/LVSRUN.ils"'),
        ("drc/skill++/DRC.ils", '"/skill++/DRCRUN.ils"'),
    ):
        text = read_skill_source(CAD_ROOT / relative)
        assert "isCallable('SICO_setWindowIcon)" in text
        assert "isCallable('SICO_windowIconRevision)" in text
        assert 'SICO_windowIconRevision()=="20260921.sico.flow.icon.v1"' in text
        assert text.index('"/skill/UI_windowIcon.il"') < text.index(
            '"/skill/UI_flowLog.il"'
        )
        assert text.index('"/skill/UI_windowIcon.il"') < text.index(callback)


def test_ipc_output_is_routed_to_tail_viewfiles_not_ciw() -> None:
    cases = (
        ("rce/skill++/RCECB.ils", "rceIpc", '"RCE"'),
        ("lvs/skill++/LVSRUN.ils", "lvsIpc", '"LVS"'),
        ("lvs/skill++/STAGECB.ils", "lvsStageIpc", "label"),
        ("drc/skill++/DRCRUN.ils", "drcIpc", '"DRC"'),
    )
    for relative, prefix, label in cases:
        text = read_skill_source(CAD_ROOT / relative)
        for suffix in ("DataHandler", "ErrHandler"):
            handler = _procedure_body(text, f"{prefix}{suffix}")
            assert "info(" not in handler
            assert "warn(" not in handler
        assert "ipcActivateBatch(cid)" in text
        assert f"GUI_flowLogOpen({label} launchLog)" in text


def test_rce_parasitic_netlist_views_are_wired_to_ipc_completion() -> None:
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    assert "(createNetlistView @initform nil)" in gui
    assert "(startRve      @initform nil)" in gui
    assert "?name           'createNetlistView" in gui
    assert '?buttonText     "Create Parasitic Netlist View"' in gui
    assert "?name           'startRve" in gui
    assert '?buttonText     "Start RVE"' in gui
    assert "inst->outputActions=makehbl('outputActions" in gui
    assert "list(inst->createNetlistView inst->startRve)" in gui
    assert "rceSyncExtractTopCell(gui->mainForm)" in gui
    assert "rceSyncStartRve(gui->mainForm)" in gui

    assert "procedure(rceSyncCreateNetlistView" in callback
    assert "procedure(rceParasiticNetlistOutputP" in callback
    assert "'(\"dspf\" \"sp\" \"spice\" \"spef\")" in callback
    create_view_sync = _procedure_body(callback, "rceSyncCreateNetlistView")
    assert "supported=rceParasiticNetlistOutputP(form~>outType~>value)" in create_view_sync
    assert "form~>createNetlistView~>invisible=!supported" in create_view_sync
    assert "form~>createNetlistView~>enabled=supported" in create_view_sync
    assert "form~>createNetlistView~>value=nil" in create_view_sync
    assert "rceNetlistViewInputSupported(form)" not in create_view_sync
    assert callback.count("rceSyncCreateNetlistView(form)") >= 3
    assert 'SICO_tomlWriteBool(outFile "create_view" createView)' in callback
    assert "procedure(rceSyncStartRve(form)" in callback
    assert 'visible=(form~>extTool~>value=="CalXRC")' in callback
    assert "form~>startRve~>invisible=!visible" in callback
    assert "form~>startRve~>enabled=visible" in callback
    assert "form~>startRve~>value=nil" in callback
    assert callback.count("rceSyncStartRve(form)") >= 2
    assert 'SICO_tomlWriteBool(outFile "start_rve"' in callback

    post = _procedure_body(callback, "rceIpcPostFunc")
    assert "viewOk=(exitStatus==0)" in post
    assert "viewRequest=nth(3 meta)" in post
    assert "protected=errset(rceCompleteViewRequest(viewRequest launchLog) t)" in post
    assert "viewOk=and(protected car(protected))" in post
    assert post.index("rceSummaryDisplay(") < post.index("rceReleaseRun(reservation)")
    assert "resultMeta=nth(4 meta)" in post
    assert "summaryResult=errset(rceSummaryDisplay(resultMeta exitStatus viewOk) t)" in post
    assert "resultOk=and(summaryResult car(summaryResult))" in post
    assert "flowLogRequest=nth(5 meta)" in post
    assert "GUI_flowLogClose(flowLogRequest)" in post
    assert "meta=rceIpcGetMeta(cid)" in post
    assert "rceIpcSetMeta(cid nil)" in post
    assert "rceForm" not in post
    assert (
        "rceIpcSetMeta(cid list(pid launchLog cmdFile viewRequest resultMeta"
        in callback
    )
    assert callback.index(
        'flowLogRequest=GUI_flowLogOpen("RCE" launchLog)'
    ) < callback.index("ipcActivateBatch(cid)")
    assert "procedure(rceParasiticViewSpec" in callback
    assert '("dspf" list("dspfText" "DSPF"))' in callback
    assert '(("sp" "spice") list("spiceText" "Spice"))' in callback
    assert 'ddMapGetViewTypeFileName("SPEF")' in callback
    assert 'else "text"' in callback
    assert "outputType=nth(3 request)" in callback
    assert 'strcat(SICO_toolsRoot() "/../bin/nl2view")' in callback
    assert 'SICO_shellQuote(importer) " --format "' in callback
    assert '" --cdslib " SICO_shellQuote(cdsLib)' in callback
    assert "procedure(rceSessionLibraryPath(libName)" in callback
    assert "ddGetObjWritePath(libObj)" in callback
    assert '" --expected-library-path " SICO_shellQuote(libraryPath)' in callback
    assert '" --copy " SICO_shellQuote(sourceFile)' in callback
    assert "procedure(rceTextViewRequest" in callback
    assert "procedure(rceNetlistViewRequest" in callback
    assert "viewRequest=rceNetlistViewRequest(" in callback
    assert "procedure(rceCdsLibPath(form)" in callback
    assert "getq(form cdsLib)" in callback
    assert "getq(field value)" in callback
    assert "ddGetUpdatedLib()" in callback
    assert "ddGetForcedLib()" in callback
    assert "and(stringp(updatedLib) strlen(updatedLib)>0)" in callback
    assert "and(stringp(forcedLib) strlen(forcedLib)>0)" in callback
    assert "and(stringp(environmentLib) strlen(environmentLib)>0)" in callback
    assert "cdslib=rceCdsLibPath(form)" in callback
    assert "requestedViewName=nth(5 request)" in callback
    assert '" --view " SICO_shellQuote(viewName)' in callback

    batch = read_skill_source(CAD_ROOT / "rce/skill++/RCEBATCH.ils")
    assert "request=rceNetlistViewRequest(" in batch
    assert "form viewTarget outputPaths finalOutputPaths outputType" in batch

    assert "isCallable('SICO_importTextCellView)" in entry
    assert "isCallable('rceSyncStartRve)" in entry
    assert "isCallable('rceIpcSetMeta)" in entry
    assert "isCallable('rceIpcGetMeta)" in entry
    assert entry.index('"/skill/DM_textCellView.il"') < entry.index(
        '"/skill++/RCECB.ils"'
    )


def test_rce_completion_summary_is_native_versioned_and_wired() -> None:
    summary_path = CAD_ROOT / "rce/skill/UI_rceSummary.il"
    summary = read_skill_source(summary_path)
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")

    assert "boundp('UIrceSummaryVersion)" in summary
    assert 'UIrceSummaryVersion=="20260919.gui.modules.v1"' in summary
    assert "procedure(rceSummaryDisplay(meta exitStatus viewOk)" in summary
    assert "success=and(exitStatus==0 viewOk" in summary
    assert "not(meta['fileOutput])" in summary
    assert "meta['netlistPaths]" in summary
    assert 'color=\\\"#188038\\\"' in summary
    assert 'color=\\\"#c62828\\\"' in summary
    assert 'color=\\\"#b26a00\\\"' in summary
    assert "procedure(rceSummaryIgnoredLvsMismatchP(meta)" in summary
    assert "procedure(rceSummaryAnalyzableP(meta success netlistReady)" in summary
    assert '"/log/lvs-ignored-mismatch"' in summary
    assert "procedure(rceSummaryHtmlEscape(value)" in summary
    assert 'case(car(request)' in summary
    assert '("group"' in summary
    assert "requestedViewName=nth(6 request)" in summary
    assert 'rceSummaryJoin(targets ", ")' in summary
    for escaped in ("&amp;", "&lt;", "&gt;", "&quot;"):
        assert escaped in summary

    assert "hiCreateHypertextField(" in summary
    assert "?name 'rceSummaryResult" in summary
    assert "procedure(rceSummaryResultCB(form)" in summary
    assert "procedure(rceSummaryOutputRecords(meta)" in summary
    assert "hiCreateLayoutForm(" in summary
    assert "concat(\"Open Netlist\")" in summary
    assert "concat(\"Copy Netlist\")" in summary
    assert "concat(\"Analyze DSPF\")" in summary
    assert "concat(\"Open Log\")" in summary
    assert "procedure(rceSummaryOpenLogCB(form)" in summary
    assert "GUI_flowLogShow(requestId)" in summary
    assert 'requestId=GUI_flowLogOpen("RCE" path)' in summary
    assert 'strcat("runUserCmd gvim -- " SICO_shellQuote(path))' in summary
    assert "hiDisplayFileDialog(" in summary
    assert "?acceptMode 'save" in summary
    assert 'system(strcat("cp -f -- " SICO_shellQuote(sourcePath)' in summary
    assert summary.count("hiSetFormButtonEnabled(form") == 5
    assert "analyzable=rceSummaryAnalyzableP(" in summary
    assert "actionable=and(form~>rceSummarySuccess ready)" in summary
    assert "(or success rceSummaryIgnoredLvsMismatchP(meta))" not in summary
    assert "form~>rceSummaryActionable=actionable" in summary
    assert "form~>rceSummaryAnalyzable=analyzable" in summary
    assert "hiIsFormDisplayed(form)" in summary
    assert "and(success not(ignoredLvsMismatch) displayed)" in summary
    assert "Displayed extraction summary" in summary

    assert "isCallable('rceSummaryUiRevision)" in entry
    assert 'rceSummaryUiRevision()=="20260911.flow.audit.v1"' in entry
    assert 'GUI_flowLogRevision()=="20260721.flow.log.owned.v2"' in entry
    assert "isCallable('GUI_flowLogClose)" in entry
    assert "isCallable('rceSummaryOpenLogCB)" in entry
    assert entry.index('"/skill/UI_rceSummary.il"') < entry.index(
        '"/skill++/RCECB.ils"'
    )
    assert "resultMeta=rceSummaryCaptureRun(" in callback
    assert "resultMeta=nth(4 meta)" in callback
    assert (
        "summaryResult=errset(rceSummaryDisplay(resultMeta exitStatus viewOk) t)"
        in callback
    )
    assert "errset(rceSummaryDisplay(resultMeta -1 nil) t)" in callback


def test_text_cellview_import_uses_registered_ddpi_view_type() -> None:
    helper = read_skill_source(CAD_ROOT / "utility/skill/DM_textCellView.il")

    assert "boundp('sicoTextCellViewVersion)" in helper
    assert "ddMapGetViewTypeFileName(viewType)" in helper
    assert 'ddGetObj(libName cellName viewName fileTail nil "w")' in helper
    assert "ddAutoCheckout(list(fileObj))" in helper
    assert 'ddLockSet(lockId "w" nil)' in helper
    assert "unwindProtect(" in helper
    assert "ddLockFree(lockId)" in helper
    assert "ddReleaseObj(fileObj)" in helper
    assert "dbOpenCellViewByType" not in helper


def test_main_flow_launchers_do_not_print_full_commands_to_ciw() -> None:
    launchers = {
        "RCE": CAD_ROOT / "rce/skill++/RCECB.ils",
        "DRC": CAD_ROOT / "drc/skill++/DRCRUN.ils",
        "LVS": CAD_ROOT / "lvs/skill++/LVSRUN.ils",
    }

    for label, path in launchers.items():
        source = read_skill_source(path)
        assert f"<INFO> {label}::CMD=" not in source
        assert f"<INFO> {label}::LOG=" in source


def test_lvs_rve_accepts_archived_config_path() -> None:
    runner = read_skill_source(CAD_ROOT / "lvs/skill++/LVSRUN.ils")
    helper = _procedure_body(runner, "lvsRunDirFromConfig")
    prompt = _procedure_body(runner, "lvsPromptOpenRve")

    assert 'rexMatchp("/log/lvs\\\\.toml$" config)' in helper
    assert 'strlen("/log/lvs.toml")' in helper
    assert 'strlen("/lvs.toml")' in helper
    assert "runDir=lvsRunDirFromConfig(config)" in prompt
    assert 'config=strcat(runDir "/log/lvs.toml")' in prompt
