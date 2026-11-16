"""Successful target labels and failed diagnostics cannot bypass source retirement."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize('change', ['display', 'edit', 'hierarchy', 'ade', 'close'])
@pytest.mark.parametrize('operation', ['resolve', 'capture', 'gone', 'poll'])
@pytest.mark.parametrize('diagnostic', ['normal', 'error', 'return'])
def test_real_identity_change_retires_once(tmp_path, change, operation, diagnostic):
    executable = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
    if not executable.is_file():
        pytest.skip('licensed dbAccess unavailable')
    root = Path(__file__).resolve().parents[3]
    body = '''
load("%s/ai/skill/AI_window_context.il")
load("%s/sico/skill/SICO_binding.il")
probeOld=makeTable("old" nil) probeNew=makeTable("new" nil)
probeOld['libName]="SicoTest" probeOld['cellName]="old" probeOld['viewName]="schematic"
probeNew['libName]="SicoTest" probeNew['cellName]="new" probeNew['viewName]="schematic"
probeDisplay=probeOld probeEdit=probeOld probeHierarchy=nil probeAde=nil probeLive=t probeGraphical=t
probeGone=nil probeDiagnostics=0 probeTimers=0
procedure(hiGetWindowList(@optional kind) when(probeLive list('win)))
procedure(hiGetCIWindow() 'ciw)
procedure(hiGetAppType(win) "Schematics XL")
procedure(axlGetWindowSession(win) probeAde)
procedure(geIsGraphicalWindow(win noWarn) probeGraphical)
procedure(geGetWindowCellView(win) probeDisplay)
procedure(geGetEditCellView(win) probeEdit)
procedure(geGetInstHierPath(win) probeHierarchy)
procedure(hiRegCloseProc(win callback) t)
procedure(hiRegTimer(callback ticks) probeTimers=probeTimers+1)
procedure(sicoSessionLive(cid) t)
procedure(sicoNativeGone(ref reason)
  unless(member(ref probeGone) probeGone=cons(ref probeGone))
  remove("target" sicoNativeTargets) t)
sicoBindings=makeTable("bindings" nil)
probeBinding=makeTable("binding" nil) probeBinding['diagnosticPath]="probe"
sicoBindings["probe"]=probeBinding
procedure(sicoDiagnostic(binding event id method target code count length)
  probeDiagnostics=probeDiagnostics+1 t)
unless(aiWindowDesignLabel(list(probeOld nil nil nil "app"))=="SicoTest/old/schematic"
  error("cellview label escaped"))
unless(stringp(aiWindowDesignLabel(list(nil nil "ade0" nil "ADE"))) error("ADE label escaped"))
%s
probeRef=aiWindowCapture('win)
unless(stringp(aiWindowEntries[probeRef]['label])
  error("capture did not retain a string design label"))
probeTarget=makeTable("target" nil) probeTarget['ref]=probeRef probeTarget['cid]="probe"
sicoNativeTargets["target"]=probeTarget
%s
%s
%s
unless(and(aiWindowEntries[probeRef]['closed] !aiWindowResolve(probeRef)
  length(probeGone)==1) error("source did not retire exactly once"))
when(aiWindowHandles['win]==probeRef error("stale handle remains bound"))
probeDisplay=probeOld probeEdit=probeOld probeHierarchy=nil probeAde=nil probeLive=t
when(aiWindowResolve(probeRef) error("old ref revived"))
aiWindowRetire(probeRef "again")
unless(length(probeGone)==1 error("duplicate retirement"))
printf("RETIRE_PASS\\n")
exit()
'''
    setup = 'probeGraphical=nil probeAde="ade0"' if change == 'ade' else ''
    mutate = {'display': 'probeDisplay=probeNew', 'edit': 'probeEdit=probeNew',
              'hierarchy': "probeHierarchy=list(list('instance 0 0 0))",
              'ade': 'probeAde="ade1"', 'close': 'probeLive=nil'}[change]
    injection = {'normal': '',
        'error': 'procedure(aiWindowDiagnostic(ref entry reason current) error("diagnostic failed"))',
        'return': 'procedure(aiWindowDiagnostic(ref entry reason current) return("wrong result"))'}[diagnostic]
    action = {'resolve': 'when(aiWindowResolve(probeRef) error("resolve returned truthy"))',
        'capture': '''if(probeLive then
  probeNext=aiWindowCapture('win)
  unless(and(probeNext!=probeRef aiWindowEntries[probeNext]
    aiWindowResolve(probeNext)=='win) error("capture returned non-reference"))
else aiWindowGone('win) when(aiWindowCapture('win) error("closed window captured")))''',
        'gone': "aiWindowGone('win)",
        'poll': 'sicoNativePoll() unless(probeTimers==1 error("poll did not reschedule"))'}[operation]
    source = tmp_path / 'retire.il'
    source.write_text(body % (root, root, setup, mutate, injection, action))
    env = {key: value for key, value in os.environ.items()
           if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'SICO_ROOT'}}
    process = subprocess.run([str(executable), '-load', str(source)], cwd=tmp_path,
                             env=env, text=True, capture_output=True, timeout=25)
    assert 'RETIRE_PASS' in process.stdout, process.stdout + process.stderr
    assert '*Error*' not in process.stdout + process.stderr


def test_retirement_diagnostic_does_not_read_a_purged_cellview(tmp_path):
    executable = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
    if not executable.is_file():
        pytest.skip('licensed dbAccess unavailable')
    root = Path(__file__).resolve().parents[3]
    source = tmp_path / 'purged-label.il'
    source.write_text('''
load("%s/ai/skill/AI_window_context.il")
ddCreateLib("PurgedLabel" "library")
probeCv=dbOpenCellViewByType("PurgedLabel" "top" "schematic" "schematic" "w")
dbSave(probeCv) dbClose(probeCv)
probeEntry=makeTable("probeEntry" nil)
probeEntry['signature]=list(probeCv nil nil nil "Schematics")
probeEntry['label]="PurgedLabel/top/schematic"
sicoBindings=makeTable("bindings" nil)
probeBinding=makeTable("binding" nil) probeBinding['diagnosticPath]="probe"
sicoBindings["probe"]=probeBinding
procedure(sicoDiagnostic(binding event id method target code count length) t)
aiWindowDiagnostic("wctx_probe" probeEntry "window_closed" nil)
unless(probeEntry['diagnosticLogged] error("diagnostic was not recorded"))
printf("PURGED_LABEL_PASS\\n")
exit()
''' % root)
    env = {key: value for key, value in os.environ.items()
           if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'SICO_ROOT'}}
    process = subprocess.run([str(executable), '-load', str(source)], cwd=tmp_path,
                             env=env, text=True, capture_output=True, timeout=25)
    output = process.stdout + process.stderr
    assert 'PURGED_LABEL_PASS' in output, output
    assert 'dbGetq: object is a purged/freed object' not in output, output
