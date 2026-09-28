from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CAD_ROOT = Path(__file__).resolve().parents[3]



@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_dspf_skill_dbaccess_probe(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")
    protocol = CAD_ROOT / "rce/skill/RCE_dspfProtocol.il"
    region_codec = CAD_ROOT / "rce/skill/RCE_dspfRegionCodec.il"
    geometry = CAD_ROOT / "rce/skill/RCE_dspfGeometry.il"
    launcher = CAD_ROOT / "rce/skill/RCE_dspfLauncher.il"
    analyzer = CAD_ROOT / "rce/skill/RCE_dspfAnalyzer.il"
    toml_helpers = CAD_ROOT / "rce/skill/RCE_toml.il"
    gui_protocol = CAD_ROOT / "common/skill/SICO_guiProtocol.il"
    flow_log_source = CAD_ROOT / "utility/skill/UI_flowLog.il"
    flow_log = tmp_path / "rce.launch.log"
    flow_log.write_text("RCE probe log\n", encoding="utf-8")
    missing_log = tmp_path / "pending.launch.log"
    menu_source = tmp_path / "menu's sample.dspf"
    skill = "\n".join(
        (
            "mockFigures=nil",
            "mockDeleted=nil",
            "mockReject=nil",
            "mockClosed=nil",
            "mockCandidates=nil",
            "mockOpenCount=0",
            "mockFlowSerial=0",
            "mockRaised=nil",
            "mockTimers=nil",
            "mockDialogSelection=nil",
            "mockIpcCommand=nil",
            "mockIpcSerial=0",
            'mockCloseProcs=makeTable("mockCloseProcs" nil)',
            "defstruct(mockHilightSet enable)",
            "defstruct(mockCellView libName cellName viewName nets)",
            "defstruct(mockWindow cellView)",
            "defstruct(mockFigure bBox)",
            "defstruct(mockNet name figs)",
            'mockCv=make_mockCellView(?libName "lib" ?cellName "cell" '
            '?viewName "layout")',
            "mockWin=make_mockWindow(?cellView mockCv)",
            "mockExistingWin=make_mockWindow(?cellView mockCv)",
            'mockOtherCv=make_mockCellView(?libName "other" ?cellName "cell" '
            '?viewName "layout")',
            "mockOtherWin=make_mockWindow(?cellView mockOtherCv)",
            "mockOpenWindow=mockWin",
            "procedure(geCreateHilightSet(cellView lpp @optional notGlobal) "
            "make_mockHilightSet())",
            "procedure(geAddHilightFig(setId figure @optional drawAll hierPath vertex) "
            "if(mockReject then nil else mockFigures=cons(figure mockFigures) figure))",
            "procedure(geDeleteHilightSet(setId) mockDeleted=t)",
            "procedure(windowp(window) t)",
            "procedure(geGetWindowCellView(window) window~>cellView)",
            "procedure(geGetEditCellView(window) window~>cellView)",
            "procedure(hiGetWindowList(kind) mockCandidates)",
            "procedure(hiViewTextFile(file @key winSpec title appName help "
            "iconPosition deviceMode pluginFactory fixedFont) "
            "mockFlowSerial=mockFlowSerial+1 make_mockWindow())",
            "procedure(hiRegCloseProc(window closeProc) "
            "mockCloseProcs[window]=closeProc t)",
            "procedure(hiEnableTailViewfile(window) t)",
            "procedure(hiScrollWindowBottom(window) t)",
            "procedure(hiRegTimer(callback tenths) "
            "mockTimers=cons(callback mockTimers) t)",
            "procedure(hiRaiseWindow(window @optional silent) mockRaised=window t)",
            "procedure(hiCloseWindow(window) mockClosed=cons(window mockClosed) "
            "GUI_flowLogWindowClosed(window) t)",
            "procedure(deOpenCellView(lib cell view viewType window mode) "
            "mockOpenCount=mockOpenCount+1 mockOpenWindow)",
            "procedure(hiDisplayFileDialog(@key dialogName workingDir filter mode "
            "modal caption acceptMode) mockDialogSelection)",
            "procedure(ipcBeginProcess(command @optional host dataHandler errHandler "
            "postFunc logFile) mockIpcCommand=command mockIpcSerial=mockIpcSerial+1 "
            "mockIpcSerial)",
            f'load("{toml_helpers}")',
            f'load("{gui_protocol}")',
            'unsetShellEnvVar("RCE_PYTHON")',
            'setShellEnvVar("CAD_PYTHON" "/cad/python")',
            'if(RCE_pythonExe()=="/cad/python" '
            'then printf("RCE_CAD_PYTHON_INHERIT_OK\\n"))',
            'setShellEnvVar("RCE_PYTHON" "/rce/python")',
            'if(RCE_pythonExe()=="/rce/python" '
            'then printf("RCE_PYTHON_OVERRIDE_OK\\n"))',
            'unsetShellEnvVar("RCE_PYTHON")',
            'unsetShellEnvVar("CAD_PYTHON")',
            'if(RCE_pythonExe()=="python3" '
            'then printf("RCE_PYTHON_DEFAULT_OK\\n"))',
            'setShellEnvVar("RCE_ANALYZER_PYTHON" "/python")',
            'setShellEnvVar("RCE_PYTHON_ENTRY" "/rce")',
            f'load("{flow_log_source}")',
            f'load("{region_codec}")',
            f'load("{protocol}")',
            f'load("{geometry}")',
            f'load("{launcher}")',
            f'load("{analyzer}")',
            f'mockDialogSelection=list("{tmp_path}" "menu\'s sample.dspf")',
            f'outFile=outfile("{menu_source}")',
            'fprintf(outFile "*|DSPF 1.0\\n")',
            "close(outFile)",
            "menuCid=rceDspfAnalyzerChooseFile()",
            "if(and(menuCid==1 index(mockIpcCommand \" dspf-gui \") "
            "!index(mockIpcCommand \"--bridge\") "
            "index(mockIpcCommand \"-u RCE_DSPF_LAYOUT_LIB\") "
            "index(mockIpcCommand \"menu'\\\"'\\\"'s sample.dspf'\")) "
            'then printf("RCE_DSPF_MENU_LAUNCH_OK\\n"))',
            "rceDspfLauncherPostFunc(menuCid 0)",
            "if(length(rceDspfLauncherProcesses)==0 "
            'then printf("RCE_DSPF_MENU_CLEANUP_OK\\n"))',
            "mockDialogSelection=nil",
            "mockIpcCommand=nil",
            "cancelled=rceDspfAnalyzerChooseFile()",
            "if(and(!cancelled !mockIpcCommand mockIpcSerial==1) "
            'then printf("RCE_DSPF_MENU_CANCEL_OK\\n"))',
            f'flowOne=GUI_flowLogOpen("RCE" "{flow_log}")',
            f'flowTwo=GUI_flowLogOpen("RCE" "{flow_log}")',
            f'flowPending=GUI_flowLogOpen("RCE" "{missing_log}")',
            "flowOneWin=cadFlowLogWindows[flowOne]",
            "flowTwoWin=cadFlowLogWindows[flowTwo]",
            "flowShown=GUI_flowLogShow(flowTwo)",
            "flowClosed=GUI_flowLogClose(flowOne)",
            "flowPendingClosed=GUI_flowLogClose(flowPending)",
            "if(and(flowOne!=flowTwo flowTwo!=flowPending flowShown flowClosed "
            "flowPendingClosed mockRaised==flowTwoWin "
            "mockCloseProcs[flowOneWin]=='GUI_flowLogWindowClosed "
            "!cadFlowLogWindows[flowOne] "
            "!cadFlowLogWindowRequests[flowOneWin] "
            "cadFlowLogWindows[flowTwo]==flowTwoWin "
            "!cadFlowLogRequests[flowPending] length(mockClosed)==1) "
            'then printf("RCE_FLOW_LOG_OWNERSHIP_OK\\n"))',
            "GUI_flowLogWindowClosed(flowTwoWin)",
            "if(and(!cadFlowLogWindows[flowTwo] "
            "!cadFlowLogWindowRequests[flowTwoWin]) "
            'then printf("RCE_FLOW_LOG_MANUAL_CLOSE_OK\\n"))',
            "mockClosed=nil",
            "figNear=make_mockFigure(?bBox list(0:0 2:2))",
            "figFar=make_mockFigure(?bBox list(10:10 12:12))",
            'netExact=make_mockNet(?name "VDD" ?figs list(figNear figFar))',
            'netCase=make_mockNet(?name "vdd" ?figs nil)',
            "mockCv~>nets=list(netExact netCase)",
            'request=rceDspfJsonParseRequest("{\\"id\\":1,\\"method\\":'
            '\\"bridge.ping\\",\\"params\\":{}}")',
            'truncated=rceDspfJsonParseString("\\"unterminated" 1)',
            'integer=rceDspfJsonParsePositiveInteger("12" 1)',
            'controls=rceDspfJsonParseString("\\\"A\\\\bB\\\\fC\\\"" 1)',
            'encoded=rceDspfJsonEscape(cadr(controls))',
            'encodedOther=rceDspfJsonEscape(strcat("A" '
            'get_pname(intToChar(1)) "B"))',
            'rawControl=rceDspfJsonParseString(strcat("\\\"A" "\\b" "B\\\"") 1)',
            'trailing=rceDspfJsonParseRequest("{\\\"id\\\":1,\\\"method\\\":'
            '\\\"bridge.ping\\\", }")',
            'regionRequest=rceDspfJsonParseRequest("{\\"id\\":2,\\"method\\":'
            '\\"oa.highlight_net\\",\\"params\\":{\\"name\\":\\"VDD\\",'
            '\\"regions\\":[[-1,-1,3,3]]}}")',
            'badRegion=rceDspfJsonParseRequest("{\\"id\\":3,\\"method\\":'
            '\\"oa.highlight_net\\",\\"params\\":{\\"name\\":\\"VDD\\",'
            '\\"regions\\":[[1+2,0,3,4]]}}")',
            "if(and(car(request) !car(truncated) "
            'nth(3 truncated)=="unterminated JSON string" '
            "car(integer) cadr(integer)==12) "
            'then printf("RCE_DSPF_PROTOCOL_RETURN_OK\\n"))',
            "if(and(car(controls) strlen(cadr(controls))==5 "
            'substring(cadr(controls) 2 1)=="\\b" '
            'substring(cadr(controls) 4 1)=="\\f" '
            'encoded=="A\\\\bB\\\\fC" '
            'encodedOther=="A\\\\u0001B") '
            'then printf("RCE_DSPF_CONTROL_ESCAPE_OK\\n"))',
            "if(and(!car(rawControl) "
            'nth(3 rawControl)=="unescaped JSON control character" '
            "!car(trailing) "
            'nth(3 trailing)=="trailing comma in request object") '
            'then printf("RCE_DSPF_STRICT_JSON_OK\\n"))',
            "if(and(car(regionRequest) "
            "length(cadr(regionRequest)['params]['regions])==1 !car(badRegion)) "
            'then printf("RCE_DSPF_REGION_PARSE_OK\\n"))',
            "selected=rceDspfSelectHighlightFigures(netExact "
            "list(list(-1 -1 3 3)) t)",
            "fallback=rceDspfSelectHighlightFigures(netExact "
            "list(list(20 20 21 21)) t)",
            'candidates=rceDspfCaseCandidates(mockCv "VdD")',
            'oaBusName=rceDspfOaName("DATA[15]")',
            'oaPlainName=rceDspfOaName("VDD")',
            "bbox=rceDspfFigureBBox(list(figNear figFar))",
            "if(and(car(selected)==list(figNear) "
            'cadr(selected)=="resistance_regions" '
            "car(fallback)==list(figNear figFar) "
            'cadr(fallback)=="full_net_fallback" '
            'candidates==list("VDD" "vdd") bbox==list(0:0 12:12) '
            'oaBusName=="DATA<15>" oaPlainName=="VDD") '
            'then printf("RCE_DSPF_GEOMETRY_OK\\n"))',
            'rceDspfAnalyzerState[\'layoutLib]="lib"',
            'rceDspfAnalyzerState[\'layoutCell]="cell"',
            'rceDspfAnalyzerState[\'layoutView]="layout"',
            "rceDspfAnalyzerState['window]=nil",
            "resolved=rceDspfLayoutWindow()",
            "if(and(resolved==mockWin rceDspfAnalyzerState['openedWindow]) "
            'then printf("RCE_DSPF_LAYOUT_RETURN_OK\\n"))',
            "rceDspfAnalyzerState['window]=nil",
            "rceDspfAnalyzerState['openedWindow]=nil",
            "mockOpenWindow=mockOtherWin",
            "rejected=rceDspfLayoutWindow()",
            "if(and(!rejected member(mockOtherWin mockClosed) "
            "!rceDspfAnalyzerState['window] "
            "!rceDspfAnalyzerState['openedWindow]) "
            'then printf("RCE_DSPF_LAYOUT_REJECT_CLEANUP_OK\\n"))',
            "mockCandidates=list(mockExistingWin)",
            "resolved=rceDspfLayoutWindow()",
            "if(and(resolved==mockExistingWin "
            "!rceDspfAnalyzerState['openedWindow] "
            "mockOpenCount==2 length(mockClosed)==1 "
            "!member(mockExistingWin mockClosed)) "
            'then printf("RCE_DSPF_LAYOUT_REUSE_OK\\n"))',
            "result=rceDspfCreateNetHighlight('cv list('pathFig 'polygonFig))",
            "setId=car(result)",
            "if(and(result cadr(result)==2 setId~>enable "
            "reverse(mockFigures)==list('pathFig 'polygonFig) !mockDeleted) "
            'then printf("RCE_DSPF_HILIGHT_OBJECTS_OK\\n"))',
            "mockReject=t",
            "mockDeleted=nil",
            "failed=rceDspfCreateNetHighlight('cv list('pathFig))",
            "if(and(!failed mockDeleted) "
            'then printf("RCE_DSPF_HILIGHT_CLEANUP_OK\\n"))',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RCE_DSPF_PROTOCOL_RETURN_OK" in output
    assert "RCE_DSPF_CONTROL_ESCAPE_OK" in output
    assert "RCE_DSPF_STRICT_JSON_OK" in output
    assert "RCE_DSPF_REGION_PARSE_OK" in output
    assert "RCE_DSPF_GEOMETRY_OK" in output
    assert "RCE_DSPF_LAYOUT_RETURN_OK" in output
    assert "RCE_DSPF_LAYOUT_REJECT_CLEANUP_OK" in output
    assert "RCE_DSPF_LAYOUT_REUSE_OK" in output
    assert "RCE_DSPF_HILIGHT_OBJECTS_OK" in output
    assert "RCE_DSPF_HILIGHT_CLEANUP_OK" in output
    assert "RCE_DSPF_MENU_LAUNCH_OK" in output
    assert "RCE_DSPF_MENU_CLEANUP_OK" in output
    assert "RCE_DSPF_MENU_CANCEL_OK" in output
    assert "RCE_FLOW_LOG_OWNERSHIP_OK" in output
    assert "RCE_FLOW_LOG_MANUAL_CLOSE_OK" in output
    assert "RCE_CAD_PYTHON_INHERIT_OK" in output
    assert "RCE_PYTHON_OVERRIDE_OK" in output
    assert "RCE_PYTHON_DEFAULT_OK" in output
