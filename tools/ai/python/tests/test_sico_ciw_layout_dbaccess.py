"""CIW positioning after a SiCo launch, against real SKILL parsing and calls."""

import json
import os
import select
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).resolve().parents[3]


def test_sico_launch_moves_ciw_to_the_lower_right_quarter(tmp_path):
    executable = shutil.which(os.environ.get("CAD_AI_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess unavailable")
    source = f"""
cadContextBuilding=t
sicoLaunchWorkingDir="/test"
load({json.dumps(str(CAD / "sico/skill/SICO_launcher.il"))})
cadContextBuilding=nil

/* Window layer stubs: every geometry decision becomes observable. */
procedure(testCIW() 'ciw)
procedure(testNoCIW() nil)
procedure(testScreenNested() list(list(2544 1245) list(673 329)))
procedure(testScreenFlat() list(2544 1245))
procedure(testScreenSmall() list(list(400 300) list(106 79)))
procedure(testScreenError() error("no screen"))
procedure(testResize(wnd box)
  testEvents=append(testEvents list(list('resize wnd box)))
  t)
procedure(testMove(wnd pt)
  testEvents=append(testEvents list(list('move wnd pt)))
  t)
putd('hiGetCIWindow getd('testCIW))
putd('hiGetScreenSize getd('testScreenNested))
putd('hiResizeWindow getd('testResize))
putd('hiMoveWindow getd('testMove))
testEvents=nil

/* The screen is split at its midpoint; SKILL window coordinates start bottom-left. */
sicoPlaceCiw()
unless(equal(testEvents list(
    list('resize 'ciw list(list(1272 0) list(2544 622)))
    list('move 'ciw list(1272 0))))
  error("lower-right CIW placement differs: %L" testEvents))
printf("CIW_LOWER_RIGHT_PASS\\n")

/* Flat screen-size returns are accepted. */
putd('hiGetScreenSize getd('testScreenFlat))
testEvents=nil
sicoPlaceCiw()
unless(equal(testEvents list(
    list('resize 'ciw list(list(1272 0) list(2544 622)))
    list('move 'ciw list(1272 0))))
  error("flat screen size rejected: %L" testEvents))
printf("CIW_FLAT_PASS\\n")

/* Missing CIW, invalid screens and undersized screens leave every window alone. */
putd('hiGetCIWindow getd('testNoCIW))
putd('hiGetScreenSize getd('testScreenNested))
testEvents=nil
sicoPlaceCiw()
unless(null(testEvents) error("placement ran without a CIW: %L" testEvents))
putd('hiGetCIWindow getd('testCIW))
putd('hiGetScreenSize getd('testScreenError))
sicoPlaceCiw()
unless(null(testEvents) error("placement ran without a screen: %L" testEvents))
putd('hiGetScreenSize getd('testScreenSmall))
sicoPlaceCiw()
unless(null(testEvents) error("placement ran on an undersized screen: %L" testEvents))
printf("CIW_GUARD_PASS\\n")

/* A Virtuoso started by SiCo sets the marker; a normal load keeps the layout. */
putd('hiGetScreenSize getd('testScreenNested))
setShellEnvVar("SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT" "lower-right-quarter")
testEvents=nil
sicoPlaceLaunchedCiw()
unless(equal(testEvents list(
    list('resize 'ciw list(list(1272 0) list(2544 622)))
    list('move 'ciw list(1272 0))))
  error("launch marker didn't place the CIW: %L" testEvents))
unless(null(sicoEnv("SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT"))
  error("launch marker was not consumed"))
printf("CIW_LAUNCHED_PASS\\n")
testEvents=nil
sicoPlaceLaunchedCiw()
unless(null(testEvents) error("normal load moved the CIW: %L" testEvents))
printf("CIW_NORMAL_KEEP_PASS\\n")

/* A successful desktop launch places the CIW; reuse and refusal do not. */
sicoBindings=makeTable("testSicoBindings" nil)
sicoBindings['bridge]=makeTable("testSicoBridge" nil)
sicoBindings['bridge]['instanceId]="instance"
sicoBindings['bridge]['generation]="generation"
sicoBindings['bridge]['targets]=makeTable("testSicoTargets" nil)
procedure(testContextJson(binding)
  binding['valid]=t
  "{{}}")
procedure(testSessionForWindow(win) testExisting)
procedure(testLaunchCommand(@optional (mode "launch")) "unused")
procedure(sicoEnsureBridge(source) 'bridge)
procedure(sicoConnect(command instanceId generation window binding targetId)
  sicoBindings['desktop]=makeTable("testSicoDesktop" nil)
  'desktop)
procedure(sicoBindSession(window cid) t)
putd('sicoContextJson getd('testContextJson))
putd('sicoSessionForWindow getd('testSessionForWindow))
putd('sicoLaunchCommand getd('testLaunchCommand))
testExisting=nil
testBinding=makeTable("testSicoBinding" nil)
testBinding['window]='ciw
testBinding['entry]="test"
testEvents=nil
unless(sicoShow(nil testBinding t t) error("new desktop launch was refused"))
unless(equal(testEvents list(
    list('resize 'ciw list(list(1272 0) list(2544 622)))
    list('move 'ciw list(1272 0))))
  error("launch did not place the CIW: %L" testEvents))
printf("CIW_LAUNCH_PASS\\n")

/* Showing an existing desktop preserves the user's CIW position. */
testExisting=ipcBeginProcess("cat > /dev/null")
testEvents=nil
unless(sicoShow(nil testBinding nil t)==testExisting
  error("existing desktop was not reused"))
unless(null(testEvents) error("existing desktop moved the CIW: %L" testEvents))
ipcKillProcess(testExisting)
testExisting=nil
printf("CIW_REUSE_PASS\\n")

/* A refused launch must not rearrange the desktop. */
procedure(sicoConnect(command instanceId generation window binding targetId) nil)
testEvents=nil
when(sicoShow(nil testBinding t t) error("failed launch reported success"))
unless(null(testEvents) error("failed launch moved the CIW: %L" testEvents))
printf("CIW_FAILURE_PASS\\n")

printf("SICO_CIW_LAYOUT_DBACCESS_OK\\n")
exit()
"""
    result = subprocess.run(
        [executable, "-nocdsinit"],
        input=source,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=45,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    for marker in ("LOWER_RIGHT", "FLAT", "GUARD", "LAUNCHED", "NORMAL_KEEP",
                   "LAUNCH", "REUSE", "FAILURE"):
        assert f"CIW_{marker}_PASS" in output, output
    assert "SICO_CIW_LAYOUT_DBACCESS_OK" in output, output
    assert "*Error*" not in output, output


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1",
    reason="licensed isolated Virtuoso GUI acceptance requires CAD_AI_RUN_GUI_TESTS=1",
)
def test_real_ciw_uses_the_lower_right_quarter_under_xvfb(tmp_path):
    result = tmp_path / "result.txt"
    source = f"""
sicoBindings=makeTable("ciwGuiProbeBindings" nil)
load({json.dumps(str(CAD / "sico/skill/SICO_launcher.il"))})
procedure(probeCiw()
  let((screen before after port)
    screen=hiGetScreenSize()
    before=hiGetAbsWindowScreenBBox(hiGetCIWindow())
    sicoPlaceCiw()
    after=hiGetAbsWindowScreenBBox(hiGetCIWindow())
    port=outfile({json.dumps(str(result))})
    fprintf(port "screen=%L\\nbefore=%L\\nafter=%L\\n" screen before after)
    close(port)
    hiRegTimer("exit()" 2)))
hiRegTimer("probeCiw()" 20)
"""
    log = _run_xvfb_probe(tmp_path, source)
    report = result.read_text()
    assert "screen=((2000 1200)" in report, report
    assert "after=((1000 0) (2000 600))" in report, report
    assert "*Error*" not in log, log


@pytest.mark.skipif(
    os.environ.get("CAD_AI_RUN_GUI_TESTS") != "1",
    reason="licensed isolated Virtuoso GUI acceptance requires CAD_AI_RUN_GUI_TESTS=1",
)
def test_sico_launched_virtuoso_places_ciw_under_xvfb(tmp_path):
    result = tmp_path / "result.txt"
    source = f"""
load({json.dumps(str(CAD.parent / "scripts/sicoAutoLoad.il"))})
procedure(probeCiw()
  let((port)
    port=outfile({json.dumps(str(result))})
    fprintf(port "after=%L\\n" hiGetAbsWindowScreenBBox(hiGetCIWindow()))
    close(port)
    hiRegTimer("exit()" 2)))
hiRegTimer("probeCiw()" 20)
"""
    log = _run_xvfb_probe(
        tmp_path,
        source,
        environment={
            "SICO_HOME": str(CAD.parent),
            "SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT": "lower-right-quarter",
        },
    )
    report = result.read_text()
    assert "after=((1000 0) (2000 600))" in report, report
    assert "*Error*" not in log, log


def _run_xvfb_probe(tmp_path, source_text, *, environment=None):
    virtuoso = shutil.which("virtuoso")
    xvfb = shutil.which("Xvfb")
    if not virtuoso or not xvfb:
        pytest.skip("Virtuoso/Xvfb unavailable")
    (tmp_path / "cds.lib").write_text("")
    source = tmp_path / "probe.il"
    source.write_text(source_text)
    rfd, wfd = os.pipe()
    xprocess = subprocess.Popen(
        [xvfb, "-displayfd", str(wfd), "-screen", "0", "2000x1200x24",
         "-nolisten", "tcp"],
        pass_fds=(wfd,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    os.close(wfd)
    process = None
    try:
        assert select.select([rfd], [], [], 10)[0], "Xvfb did not report a display"
        display = os.read(rfd, 32).decode().strip()
        assert display.isdigit()
        launch_environment = dict(os.environ, DISPLAY=":" + display)
        launch_environment.update(environment or {})
        with (tmp_path / "process.log").open("w") as log:
            process = subprocess.Popen(
                [virtuoso, "-nocdsinit", "-norestore", "-restore", str(source),
                 "-log", "CDS.log"],
                cwd=tmp_path,
                env=launch_environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        assert process.wait(timeout=45) == 0, (tmp_path / "process.log").read_text()
        return (tmp_path / "process.log").read_text()
    finally:
        os.close(rfd)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        if xprocess.poll() is None:
            xprocess.terminate()
            xprocess.wait(timeout=5)
