"""OA targets retain source identity when editor products gain ADE associations."""

import json
import os
from pathlib import Path
import subprocess

import pytest

TOOLS = Path(__file__).resolve().parents[3]
DB = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess')
pytestmark = pytest.mark.skipif(not DB.is_file(), reason='licensed dbAccess unavailable')


def probe(tmp_path, body):
    source = tmp_path / 'identity.il'
    loader = json.dumps(str(TOOLS / 'sico/skill++/SICO.ils'))
    source.write_text('load(' + loader + ')\n' + r'''
ddCreateLib("SicoTest" strcat(getWorkingDir() "/SicoTest"))
procedure(identityCv(icType)
  let((icCv)
    icCv=dbOpenCellViewByType("SicoTest" "dut" strcat("custom_" icType) icType "w")
    unless(icCv error("OA fixture failed")) dbSave(icCv) icCv))
probeCvs=list(identityCv("schematic") identityCv("schematicSymbol") identityCv("maskLayout"))
probeDisplay=car(probeCvs) probeEdit=probeDisplay probeAssociated=nil probeHierarchy=nil
probeApp="Schematics" probeGone=nil
procedure(hiGetWindowList(@optional kind) list('editor 'ade 'ciw 'text 'empty))
procedure(hiGetCIWindow() 'ciw)
procedure(hiGetCurrentWindow() 'ciw)
procedure(hiGetAppType(win) if(win=='editor probeApp "arbitrary application label"))
procedure(hiGetWindowName(win) "probe")
procedure(geIsGraphicalWindow(win noWarn)
  unless(noWarn error("warning suppression missing")) member(win list('editor 'empty)))
procedure(geGetWindowCellView(win)
  unless(member(win list('editor 'empty)) error("non-graphical GE read"))
  when(win=='editor probeDisplay))
procedure(geGetEditCellView(win)
  unless(member(win list('editor 'empty)) error("non-graphical GE read"))
  when(win=='editor probeEdit))
procedure(geGetInstHierPath(win) probeHierarchy)
procedure(axlGetWindowSession(win)
  cond((win=='ade "ade0") (member(win list('editor 'empty 'ciw)) probeAssociated) (t nil)))
procedure(axlGetSessionLibName(session) "SicoTest")
procedure(axlGetSessionCellName(session) "dut")
procedure(axlGetSessionViewName(session) "maestro")
procedure(hiRegCloseProc(win callback) t)
procedure(sicoNativeGone(ref reason) probeGone=cons(list(ref reason) probeGone) t)
procedure(identityBinding(ibWindow)
  let((ibBinding) ibBinding=makeTable("binding" nil) ibBinding['window]=ibWindow
    ibBinding['entry]="identity_probe" ibBinding))
''' + body + '\nprintf("IDENTITY_PASS\\n") exit()\n')
    env = {key: value for key, value in os.environ.items()
           if key not in {'LD_LIBRARY_PATH', 'LD_PRELOAD', 'SICO_ROOT'}}
    result = subprocess.run([str(DB), '-load', str(source)], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=30)
    output = result.stdout + result.stderr
    (tmp_path / 'probe.log').write_text(output)
    assert 'IDENTITY_PASS' in output, output
    diagnostics = '\n'.join(line for line in output.splitlines()
                            if not line.startswith('IDENTITY_JSON '))
    assert all(text not in diagnostics for text in ('*Error*', 'GE-2067', 'PARSER WARNING')), output
    return [json.loads(line.split('IDENTITY_JSON ', 1)[1])
            for line in output.splitlines() if line.startswith('IDENTITY_JSON ')]


def test_oa_type_and_context_survive_capability_and_ade_association_changes(tmp_path):
    rows = probe(tmp_path, r'''
foreach(probeCv probeCvs
  probeDisplay=probeCv probeEdit=probeCv probeAssociated=nil probeApp="base"
  probeBinding=identityBinding('editor)
  probeInitial=sicoContextJson(probeBinding)
  unless(probeBinding['valid] error("initial context invalid"))
  probeRef=probeBinding['sharedWindowRef] probeGone=nil
  foreach(probeApp list("Schematics" "Schematics XL" "explorer-schematic"
    "assembler-schematic" "Layout L" "Virtuoso XL" "Layout GXL" "future-editor")
    foreach(probeAssociated list(nil "ade0" "ade1" nil)
      unless(aiWindowCapture('editor)==probeRef error("capture changed identity"))
      unless(aiWindowResolve(probeRef)=='editor error("resolve retired identity"))
      unless(sicoContextJson(probeBinding)==probeInitial error("captured context changed"))
      unless(and(probeBinding['valid] !probeBinding['adeSession]) error("editor became ADE"))
      when(or(probeGone aiWindowAdeSession('editor)) error("incidental association became owner"))))
  printf("IDENTITY_JSON %s\n" sicoContextJson(probeBinding))
  printf("IDENTITY_JSON %s\n" aiPdkJson(aiWindowRow('editor probeRef))))
probeAde=identityBinding('ade)
printf("IDENTITY_JSON %s\n" sicoContextJson(probeAde))
probeAssociated="ade0"
foreach(probeAux list('text 'empty)
  probeInvalid=identityBinding(probeAux)
  sicoContextJson(probeInvalid)
  when(or(probeInvalid['valid] aiWindowCapture(probeAux)) error("auxiliary target captured")))
probeCiw=identityBinding('ciw)
printf("IDENTITY_JSON %s\n" sicoContextJson(probeCiw))
''')
    for index, (oa_view_type, kind) in enumerate([('schematic', 'schematic'),
            ('schematicSymbol', 'symbol'), ('maskLayout', 'layout')]):
        context, window = rows[index * 2:index * 2 + 2]
        assert context['valid'] and context['ade_session'] == ''
        assert context['cellview'] == dict(
            lib='SicoTest', cell='dut', view_name='custom_' + oa_view_type,
            oa_view_type=oa_view_type, oa_view_type_source='platform_api',
        )
        assert context['selection_scope'] == 'window'
        assert 'read_ade_setup' not in context['capabilities']
        assert window['kind'] == kind and window['ade_session'] is None
        assert window['cellview']['view_name'] == 'custom_' + oa_view_type
        assert window['cellview']['oa_view_type'] == oa_view_type
        assert window['window_ref'] == context['window_ref']
    assert rows[6]['valid'] and rows[6]['ade_session'] == 'ade0'
    assert rows[6]['cellview']['view_name'] == 'maestro'
    assert rows[6]['cellview']['oa_view_type'] is None
    assert rows[6]['selection_scope'] == 'ade_session'
    assert rows[7]['valid'] and rows[7]['cellview'] is None and rows[7]['ade_session'] == ''


def test_ask_context_stays_stale_after_real_target_returns(tmp_path):
    probe(tmp_path, r'''
probeBinding=identityBinding('editor) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef]
probeDisplay=cadr(probeCvs) probeEdit=probeDisplay
sicoContextJson(probeBinding)
when(probeBinding['valid] error("changed target stayed valid"))
unless(length(probeGone)==1 error("retirement not published once"))
probeDisplay=car(probeCvs) probeEdit=probeDisplay
sicoContextJson(probeBinding)
when(or(probeBinding['valid] aiWindowResolve(probeRef)) error("stale binding revived"))
probeNew=identityBinding('editor) sicoContextJson(probeNew)
unless(and(probeNew['valid] probeNew['sharedWindowRef]!=probeRef) error("fresh capture failed"))
''')


def test_ciw_and_ade_context_never_probe_graphical_cellview_getter(tmp_path):
    rows = probe(tmp_path, r'''
foreach(probeKind list('ciw 'ade)
  probeGeReads=0
  procedure(geGetWindowCellView(win)
    probeGeReads=probeGeReads+1
    nil)
  probeBinding=identityBinding(probeKind)
  printf("IDENTITY_JSON %s\n" sicoContextJson(probeBinding))
  unless(probeGeReads==0
    error("Non-graphical context called geGetWindowCellView: %L" probeKind)))
''')
    assert rows[0]["valid"] is True and rows[0]["oa_view_type"] is None
    assert rows[1]["valid"] is True and rows[1]["oa_view_type"] is None
