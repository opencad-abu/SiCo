"""Window presentation control never turns passive state changes into focus requests."""
import os
import queue
import threading
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication
from sico_ui.lifecycle import DesktopControl
from sico_ui.composer import QuickComposer
from sico_ui.window import AssistantWindow


def test_skill_window_capability_retirement_and_reconnect_are_bounded(tmp_path):
    import subprocess
    from pathlib import Path
    import pytest

    root = Path(__file__).resolve().parents[3]
    executable = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
    if not executable.is_file():
        pytest.skip('licensed dbAccess is unavailable')
    source = tmp_path / 'window-churn.il'
    source.write_text('''
load("%s/sico/skill++/SICO.ils")
probeGeCalls=0 probeTimers=nil probeSpawns=0
procedure(hiGetWindowList(@optional kind) list('fileWindow 'editorWindow))
procedure(hiGetCIWindow() 'ciwWindow)
procedure(hiGetAppType(win) if(win=='fileWindow "Show File" "Schematics"))
procedure(axlGetWindowSession(win) nil)
procedure(geIsGraphicalWindow(win noWarn)
  unless(noWarn error("Capability probe must suppress warnings")) win=='editorWindow)
procedure(geGetWindowCellView(win) probeGeCalls=probeGeCalls+1 'cellView)
procedure(geGetEditCellView(win) probeGeCalls=probeGeCalls+1 'cellView)
procedure(geGetInstHierPath(win) nil)
procedure(hiRegCloseProc(win callback) t)
procedure(hiRegTimer(callback ticks) probeTimers=cons(ticks probeTimers))
probeCid=ipcBeginProcess("cat > /dev/null")
procedure(sicoLaunchCommand(@optional mode) probeSpawns=probeSpawns+1 "unused")
for(i 1 30
  unless(aiWindowSignature('fileWindow)==list(nil nil nil nil "Show File") error("Bad signature"))
  when(aiWindowCapture('fileWindow) error("Non-graphical window captured")))
unless(probeGeCalls==0 error("GE getter called for non-graphical window"))
probeBinding=makeTable("binding" nil)
probeBinding['instanceId]="i" probeBinding['generation]="g"
sicoBindings[probeCid]=probeBinding
probeRef=aiWindowCapture('editorWindow)
unless(probeRef error("Graphical editor was not captured"))
probeBinding['sharedWindowRef]=probeRef
sicoNativeRecord(probeCid "target" probeBinding "{}")
aiWindowGone('editorWindow) aiWindowGone('editorWindow)
for(i 1 30 sicoNativePoll())
unless(and(!aiWindowHandles['editorWindow]
  !sicoNativeTargets["g_target"] !aiWindowResolve(probeRef)) error("Close not terminal"))
probeTimers=nil probeBinding['skillActive]=t sicoInstanceBridge=probeCid
probeCopy=makeTable("pending" nil)
sicoReconnectPending=list(probeCid probeCopy)
for(i 1 30 sicoFinishReconnect())
unless(and(!sicoReconnectPending reverse(probeTimers)==list(2 5 10)) error("Unbounded reconnect"))
procedure(sicoContextJson(binding) binding['valid]=t "{}")
procedure(sicoSessionForWindow(win) nil)
when(sicoShow('editorWindow probeBinding) error("Dead desktop restored"))
unless(probeSpawns==0 error("Passive show spawned a desktop"))
ipcKillProcess(probeCid)
printf("WINDOW_CHURN_PASS\\n")
exit()
''' % root)
    env = {key: value for key, value in os.environ.items()
           if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'SICO_ROOT'}}
    result = subprocess.run([str(executable), '-load', str(source)], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=25)
    output = result.stdout + result.stderr
    assert 'WINDOW_CHURN_PASS' in output, output
    assert '*Error*' not in output and 'GE-2067' not in output, output


def test_application_type_change_keeps_stable_window_identity(tmp_path):
    import subprocess
    from pathlib import Path
    import pytest

    root = Path(__file__).resolve().parents[3]
    executable = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
    if not executable.is_file():
        pytest.skip('licensed dbAccess is unavailable')
    source = tmp_path / 'app-type.il'
    source.write_text('''
load("%s/ai/skill/AI_window_context.il")
probeApp="Schematics" probeAssociated=nil
probeStable=makeTable("stable" nil)
probeStable['cellViewType]="schematic"
probeStable['libName]="SicoTest" probeStable['cellName]="old" probeStable['viewName]="schematic"
probeChanged=makeTable("changed" nil)
probeChanged['libName]="SicoTest" probeChanged['cellName]="new" probeChanged['viewName]="schematic"
probeCellView=probeStable
procedure(hiGetWindowList(@optional kind) list('schWindow 'layoutWindow 'ciwWindow))
procedure(hiGetCIWindow() 'ciwWindow)
procedure(hiGetCurrentWindow() 'schWindow)
procedure(hiGetAppType(win) probeApp)
procedure(axlGetWindowSession(win) probeAssociated)
procedure(geIsGraphicalWindow(win noWarn) win=='schWindow)
procedure(geGetWindowCellView(win) probeCellView)
procedure(geGetEditCellView(win) probeCellView)
procedure(geGetInstHierPath(win) nil)
procedure(hiRegCloseProc(win callback) t)
probeGone=nil
procedure(sicoNativeGone(ref reason) probeGone=cons(list(ref reason) probeGone) t)
probeRef=aiWindowCapture('schWindow)
unless(probeRef error("capture failed"))
probeApp="explorer-schematic" probeAssociated="fnxSession0"
unless(aiWindowResolve(probeRef) error("app type change retired source"))
unless(aiWindowCapture('schWindow)==probeRef error("capture replaced stable ref"))
probeApp="Layout XL"
unless(aiWindowResolve(probeRef) error("layout app type change retired source"))
when(probeGone error("app type change notified native retirement"))
probeDiagnostics=nil
sicoBindings=makeTable("probeBindings" nil)
probeBinding=makeTable("probeBinding" nil) probeBinding['diagnosticPath]="probe"
sicoBindings["probe"]=probeBinding
procedure(sicoDiagnostic(binding event id method target code count length)
  probeDiagnostics=cons(list(event id method target code) probeDiagnostics) t)
probeCellView=probeChanged
when(aiWindowResolve(probeRef) error("real target change did not retire source"))
unless(and(length(probeDiagnostics)==1
  rexMatchp("changed=display" nth(4 car(probeDiagnostics))))
  error("target invalidation diagnostic missing or duplicated"))
unless(and(length(probeGone)==1 cadr(car(probeGone))=="window_closed_or_changed")
  error("target change did not notify native retirement once"))
probeCellView=probeStable
when(aiWindowResolve(probeRef) error("retired source was revived"))
printf("APP_TYPE_STABLE_PASS\\n")
exit()
''' % root)
    env = {key: value for key, value in os.environ.items()
           if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'SICO_ROOT'}}
    result = subprocess.run([str(executable), '-load', str(source)], cwd=tmp_path,
                            env=env, text=True, capture_output=True, timeout=25)
    output = result.stdout + result.stderr
    assert 'APP_TYPE_STABLE_PASS' in output, output
    assert '*Error*' not in output, output


def test_show_control_is_debounced_but_later_explicit_show_works(monkeypatch):
    app = QApplication.instance() or QApplication([])
    reader = SimpleNamespace(closed=threading.Event(), lines=queue.Queue(), close=lambda **_: None)
    control = DesktopControl(reader)
    shown = []
    control.window = SimpleNamespace(restore=lambda: shown.append(True))
    clock = [5.0]
    monkeypatch.setattr('sico_ui.lifecycle.time.monotonic', lambda: clock[0])
    try:
        for _ in range(10):
            reader.lines.put({'kind': 'show'})
        control.poll()
        assert shown == [True]
        clock[0] = 6.1
        reader.lines.put({'kind': 'show'})
        control.poll()
        assert shown == [True, True]
    finally:
        control.close()


def test_explicit_restore_keeps_maximized_window_and_focuses():
    calls = []
    window = SimpleNamespace(isMinimized=lambda: False, isVisible=lambda: True,
                             showNormal=lambda: calls.append('normal'),
                             show=lambda: calls.append('show'),
                             raise_=lambda: calls.append('raise'),
                             activateWindow=lambda: calls.append('activate'),
                             page=SimpleNamespace(session=SimpleNamespace(session_id='desktop')),
                             lifecycle=SimpleNamespace(
                                 notice=lambda kind, **_payload: calls.append(kind)))
    AssistantWindow.restore(window)
    assert calls == ['raise', 'activate', 'shown']


def test_composer_passive_failure_keeps_hidden_draft(monkeypatch):
    app = QApplication.instance() or QApplication([])
    window = QuickComposer({'source': 'test'}, lambda _: None)
    calls = []
    monkeypatch.setattr(window, 'restore', lambda: calls.append('restore'))
    window.input.setPlainText('keep draft')
    try:
        window.receive({'kind': 'rejected', 'message': 'failed'})
        window.disconnected()
        assert not window.isVisible() and not calls
        assert window.input.toPlainText() == 'keep draft'
        window.receive({'kind': 'show'})
        assert calls == ['restore']
    finally:
        window.finished = True
        window.close()


def test_native_show_file_is_not_captured_and_editor_still_resolves(tmp_path, monkeypatch):
    import json
    import pytest
    from circuit_create_support import AI_ROOT, session

    if os.environ.get('CAD_AI_RUN_GUI_TESTS') != '1':
        pytest.skip('licensed isolated Virtuoso window acceptance')
    monkeypatch.delenv('QT_QPA_PLATFORM', raising=False)
    monkeypatch.delenv('QT_PLUGIN_PATH', raising=False)
    with session(tmp_path) as (bridge, server, process):
        bridge.skill('load(' + json.dumps(str(AI_ROOT.parent / 'sico/skill++/SICO.ils')) + ')')
        result = bridge.skill('''let((fileWin ref binding)
          fileWin=hiCreateWindow(list(0:0 300:200) "text" "Show File")
          binding=makeTable("probeFileBinding" nil) binding['window]=fileWin
          for(i 1 30
            when(aiWindowCapture(fileWin) error("Show File acquired a reference"))
            aiWindowSignature(fileWin)
            sicoContextJson(binding)
            when(binding['valid] error("Show File is a valid agent target")))
          hiCloseWindow(fileWin)
          probeCv=dbOpenCellViewByType("genericWork" "probe" "schematic" "schematic" "w")
          dbSave(probeCv) dbClose(probeCv)
          probeWin=deOpenCellView("genericWork" "probe" "schematic" "schematic" nil "r")
          ref=aiWindowCapture(probeWin)
          unless(and(ref aiWindowResolve(ref)) error("Editor capture failed"))
          hiCloseWindow(probeWin)
          when(aiWindowResolve(ref) error("Closed editor still resolves"))
          "NATIVE_WINDOW_CHURN_PASS")''')
        assert result == 'NATIVE_WINDOW_CHURN_PASS'
        text = (tmp_path / 'CDS.log').read_text(errors='replace')
        assert 'GE-2067' not in text
