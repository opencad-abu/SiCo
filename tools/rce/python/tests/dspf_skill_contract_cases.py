from __future__ import annotations

from pathlib import Path

CAD_ROOT = Path(__file__).resolve().parents[3]


from dspf_skill_fixtures import _has_balanced_skill_parentheses, _source


def test_dspf_skill_sources_have_balanced_reader_delimiters() -> None:
    for relative in (
        "utility/skill/UI_flowLog.il",
        "rce/skill/RCE_dspfRegionCodec.il",
        "rce/skill/RCE_dspfProtocol.il",
        "rce/skill/RCE_dspfGeometry.il",
        "rce/skill/RCE_dspfLauncher.il",
        "rce/skill/RCE_dspfAnalyzer.il",
        "rce/skill/UI_rceSummary.il",
        "rce/skill++/RCE.ils",
    ):
        assert _has_balanced_skill_parentheses(_source(relative)), relative



def test_dspf_bridge_protocol_and_process_are_bounded() -> None:
    region_codec = _source("rce/skill/RCE_dspfRegionCodec.il")
    protocol = _source("rce/skill/RCE_dspfProtocol.il")
    geometry = _source("rce/skill/RCE_dspfGeometry.il")
    launcher = _source("rce/skill/RCE_dspfLauncher.il")
    analyzer = _source("rce/skill/RCE_dspfAnalyzer.il")
    gui_protocol = _source("common/skill/SICO_guiProtocol.il")

    for source in (region_codec, protocol, geometry, launcher, analyzer):
        assert len(source.splitlines()) <= 300
    assert 'rceDspfRegionCodecVersion=="20260721.dspf.regions.v1"' in region_codec
    assert "boundp('rceDspfProtocolVersion)" in protocol
    assert 'rceDspfProtocolVersion=="20260721.dspf.protocol.v3"' in protocol
    assert 'rceDspfGeometryVersion=="20260721.dspf.geometry.v1"' in geometry
    assert 'rceDspfLauncherVersion=="20260814.dspf.launcher.v5"' in launcher
    assert "boundp('rceDspfAnalyzerVersion)" in analyzer
    assert 'rceDspfAnalyzerVersion=="20260722.dspf.analyzer.v5"' in analyzer

    for unsafe in ("evalstring", "linereadstring", "loadstring"):
        for source in (region_codec, protocol, geometry, launcher, analyzer):
            assert unsafe not in source.lower()
    assert "unsupported JSON escape" in protocol
    assert "unescaped JSON control character" in protocol
    assert "trailing comma in request object" in protocol
    assert '("b"  output=strcat(output "\\b"))' in protocol
    assert '("f"  output=strcat(output "\\f"))' in protocol
    assert 'sprintf(nil "\\\\u%04x" code)' in protocol
    assert "unsupported request field" in protocol
    assert "regions exceed the 256-box limit" in region_codec
    assert "strlen(line)>65536" in analyzer
    assert "rceDspfAnalyzerState['buffer]" in analyzer
    assert 'ipcWriteProcess(cid strcat(payload "\\n"))' in protocol
    for method in (
        "oa.highlight_net",
        "oa.current_net",
        "gui.select_net",
        "bridge.ping",
    ):
        assert method in analyzer

    assert 'SICO_envValue("RCE_ANALYZER_PYTHON")' in analyzer
    assert '" dspf-gui --bridge stdio " SICO_shellQuote(source)' in analyzer
    assert "rceDspfLaunchEnvironment(nil)" in analyzer
    assert "SICO_guiPythonEnvironment(" in launcher
    assert "-u QT_QPA_PLATFORM_PLUGIN_PATH" in gui_protocol
    assert "-u QT_PLUGIN_PATH" in gui_protocol
    assert '"RCE_DSPF_PARENT_PID="' in launcher
    assert 'SICO_shellQuote(sprintf(nil "%d" parentPid))' in launcher
    assert '(SICO_shellQuote(sprintf(nil "%d" parentPid)))' not in launcher
    assert '"exec env -u PYTHONHOME' in gui_protocol
    assert "regExitBefore('rceDspfExitCleanup)" in launcher
    assert "rceDspfExitCleanupRegistered" in launcher
    assert "errset(rceDspfAnalyzerStop() t)" in launcher
    assert "errset(rceDspfLauncherStopAll() t)" in launcher
    assert "ipcBeginProcess(command" in analyzer
    assert "'rceDspfAnalyzerDataHandler" in analyzer
    assert "'rceDspfAnalyzerErrHandler" in analyzer
    assert "'rceDspfAnalyzerPostFunc" in analyzer
    # Batch mode suppresses stdout callbacks and would sever the JSONL bridge.
    assert "ipcActivateBatch" not in analyzer



def test_dspf_launcher_uses_a_native_chooser_without_oa_bridge() -> None:
    launcher = _source("rce/skill/RCE_dspfLauncher.il")

    assert "procedure(rceDspfAnalyzerChooseFile()" in launcher
    assert "selection=hiDisplayFileDialog(" in launcher
    assert "?mode 'existingFile" in launcher
    assert "?modal t" in launcher
    assert "?acceptMode 'open" in launcher
    assert "SICO_absolutePath(strcat(directory \"/\" fileName))" in launcher
    assert '" dspf-gui " SICO_shellQuote(path)' in launcher
    assert "--bridge" not in launcher
    for variable in (
        "RCE_DSPF_LAYOUT_LIB",
        "RCE_DSPF_LAYOUT_CELL",
        "RCE_DSPF_LAYOUT_VIEW",
    ):
        assert f"-u {variable}" in launcher



def test_rce_and_analyzer_inherit_the_shared_python() -> None:
    helper = _source("rce/skill/RCE_toml.il")
    entry = _source("rce/python/rce")
    launcher = _source("rce/skill/RCE_dspfLauncher.il")
    analyzer = _source("rce/skill/RCE_dspfAnalyzer.il")
    bash_env = _source("rce/env/and2x1h7_rce_env.bash")
    csh_env = _source("rce/env/and2x1h7_rce_env.csh")
    helper_start = helper.index("procedure(RCE_pythonExe()")
    helper_end = helper.index("procedure(RCE_tomlRevision()", helper_start)
    python_helper = helper[helper_start:helper_end]

    assert 'rceTomlVersion=="20260818.common.compat.v2"' in helper
    assert 'rceTomlVersion="20260818.common.compat.v2"' in helper
    assert 'SICO_tomlRevision()=="20260922.sico.temp.v6"' in helper
    assert entry.startswith("#!/usr/bin/env python3\n")
    assert 'SICO_envValue("RCE_PYTHON")' in python_helper
    assert 'SICO_envValue("SICO_PYTHON")' in python_helper
    assert 'SICO_envValue("SICO_PYTHON_ROOT")' in python_helper
    assert 'strcat(pythonRoot "/bin/python3")' in python_helper
    assert '"python3"' in python_helper
    for source in (launcher, analyzer):
        assert 'SICO_envValue("RCE_ANALYZER_PYTHON")' in source
        assert "RCE_pythonExe()" in source
    assert 'RCE_PYTHON=${CAD_PYTHON_ROOT%/}/bin/python3' in bash_env
    assert 'RCE_PYTHON=python3' in bash_env
    assert 'setenv RCE_PYTHON "$CAD_PYTHON"' in csh_env
    assert 'setenv RCE_PYTHON "$CAD_PYTHON_ROOT/bin/python3"' in csh_env
    assert "setenv RCE_PYTHON python3" in csh_env
    assert 'RCE_RUN_ROOT="${RCE_RUN_ROOT:-${PROJECT_ROOT}/.rce}"' in bash_env
    assert 'setenv RCE_RUN_ROOT "$PROJECT_ROOT/.rce"' in csh_env
    assert 'RCE_RUN_ROOT="${TMPDIR}/rce"' not in bash_env
    assert 'setenv RCE_RUN_ROOT "$TMPDIR/rce"' not in csh_env



def test_dspf_bridge_uses_only_the_captured_layout_context() -> None:
    analyzer = _source("rce/skill/RCE_dspfAnalyzer.il")
    geometry = _source("rce/skill/RCE_dspfGeometry.il")
    summary = _source("rce/skill/UI_rceSummary.il")

    for field in ("layoutLib", "layoutCell", "layoutView"):
        assert f"meta['{field}]" in summary
        assert f"rceDspfAnalyzerState['{field}]" in analyzer
    assert "hiGetWindowList('window)" in analyzer
    assert "geGetWindowCellView(window)" in analyzer
    assert "editCellView==cellView" in analyzer
    assert '"" nil "r")' in analyzer
    assert "oaName=rceDspfOaName(name)" in analyzer
    assert "dbFindNetByName(cellView oaName)" in analyzer
    assert analyzer.index("dbFindNetByName(cellView oaName)") < analyzer.index(
        "rceDspfSelectHighlightFigures(net regions hasRegions)"
    )
    assert 'geCreateHilightSet(cellView list("y0" "drawing") nil)' in geometry
    assert "geAddHilightFig(highlightSet figure nil)" in geometry
    assert "geDeleteHilightSet(highlightSet)" in geometry
    assert "highlightSet~>enable=t" in geometry
    assert "rceDspfCaseCandidates(cellView oaName)" in analyzer
    assert '"full_net_fallback"' in geometry
    assert "rceDspfFigureBBox(figures)" in analyzer
    assert "hiZoomIn(window bbox)" in analyzer
    assert "rceDspfAnalyzerState['highlightSet]" in analyzer
    assert "prog((window candidate opened)" in analyzer
    assert "when(window opened=t)" in analyzer
    assert "when(and(opened window windowp(window))" in analyzer
    assert "errset(hiCloseWindow(window) t)" in analyzer
    assert "rceDspfAnalyzerState['openedWindow]=opened" in analyzer
    assert "rceDspfAnalyzerState['window]=window" in analyzer
    assert "geRefreshWindow(window)" in analyzer
    for coordinate_probe in (
        "car(net~>figs)",
        "centerBox(",
        "geAddNetProbe(",
        "geDeleteNetProbe(",
        "geDeleteAllProbe(",
    ):
        assert coordinate_probe not in analyzer
    assert "geGetSelSet(window)" in analyzer



def test_dspf_summary_and_loader_rebuild_and_wire_the_analyzer() -> None:
    summary = _source("rce/skill/UI_rceSummary.il")
    entry = _source("rce/skill++/RCE.ils")

    assert "hiFormClose(form)" in summary
    assert "hiDeleteForm(rceSummaryForm)" not in summary
    assert "rceSummaryForm=nil" in summary
    assert "rceSummaryDspfOutputP(meta)" in summary
    assert "rceSummaryAnalyzableP(meta success netlistReady)" in summary
    assert "form~>rceSummaryAnalyzable" in summary
    assert "rceDspfAnalyzerStart(path meta)" in summary
    assert 'rceDspfRegionCodecRevision()=="20260721.dspf.regions.v1"' in entry
    assert 'rceDspfProtocolRevision()=="20260721.dspf.protocol.v3"' in entry
    assert 'rceDspfGeometryRevision()=="20260721.dspf.geometry.v1"' in entry
    assert 'rceDspfLauncherRevision()=="20260814.dspf.launcher.v5"' in entry
    assert 'SICO_guiProtocolRevision()=="20260814.cadgui.v1"' in entry
    assert "isCallable('rceDspfAnalyzerChooseFile)" in entry
    assert 'rceDspfAnalyzerRevision()=="20260722.dspf.analyzer.v5"' in entry
    assert entry.index('"/skill/RCE_dspfRegionCodec.il"') < entry.index(
        '"/skill/RCE_dspfProtocol.il"'
    )
    assert entry.index('"/skill/RCE_dspfProtocol.il"') < entry.index(
        '"/skill/RCE_dspfGeometry.il"'
    )
    assert entry.index('"/skill/RCE_dspfGeometry.il"') < entry.index(
        '"/skill/RCE_dspfLauncher.il"'
    )
    assert entry.index('"/skill/RCE_dspfLauncher.il"') < entry.index(
        '"/skill/RCE_dspfAnalyzer.il"'
    )
    assert entry.index('"/skill/RCE_dspfAnalyzer.il"') < entry.index(
        '"/skill/UI_rceSummary.il"'
    )
    assert entry.index('"/skill/UI_rceSummary.il"') < entry.index(
        '"/skill++/RCECB.ils"'
    )
