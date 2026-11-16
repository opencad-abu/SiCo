"""Transient identity reads preserve exact source references and immutable captures."""

import pytest

from test_ask_editor_identity import probe


@pytest.mark.parametrize('getter,window', [
    ('hiGetWindowList', 'editor'), ('hiGetCIWindow', 'editor'),
    ('geIsGraphicalWindow', 'editor'), ('geGetWindowCellView', 'editor'),
    ('geGetEditCellView', 'editor'), ('geGetInstHierPath', 'editor'),
    ('axlGetWindowSession', 'ade'), ('axlGetSessionCellName', 'ade'),
])
def test_read_error_keeps_binding_until_recovered(tmp_path, getter, window):
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('%s) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef]
probeOriginal=getd('%s)
putd('%s lambda((@rest ignored) error("temporary getter failure")))
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
when(aiWindowEntries[probeRef]['closed] error("temporary failure retired source"))
probeCid='probe sicoBindings[probeCid]=probeBinding
probeTarget=makeTable("target" nil) probeTarget['cid]=probeCid probeTarget['ref]=probeRef
sicoNativeTargets["transient"]=probeTarget
procedure(sicoSessionLive(cid) t)
probeTimers=0 procedure(hiRegTimer(callback delay) probeTimers=probeTimers+1)
sicoNativePoll()
unless(and(sicoNativeTargets["transient"] probeTimers==1 !probeGone)
  error("poll retired transient source or stopped scheduling"))
putd('%s probeOriginal)
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
unless(and(probeBinding['valid] probeBinding['sharedWindowRef]==probeRef
  aiWindowCapture('%s)==probeRef !probeGone) error("recovery replaced source"))
''' % (window, getter, getter, getter, window))
    assert rows[0]['valid'] is False and rows[0]['code'] == 'context_unavailable'
    assert rows[1]['valid'] is True
    assert rows[0]['window_ref'] == rows[1]['window_ref']


def test_first_failed_capture_is_retryable_and_nil_identity_values_are_valid(tmp_path):
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('editor)
probeOriginal=getd('geGetEditCellView)
procedure(geGetEditCellView(win) error("temporary initial read"))
printf("IDENTITY_JSON %s\n" sicoContextJson(probeBinding))
when(or(probeBinding['sharedWindowCaptured] probeBinding['cellJson]
  probeBinding['adeSession]) error("failed read latched target"))
putd('geGetEditCellView probeOriginal)
printf("IDENTITY_JSON %s\n" sicoContextJson(probeBinding))
unless(and(probeBinding['valid] probeBinding['sharedWindowRef]) error("first read never recovered"))
; A normal nil hierarchy and unrelated nil ADE association are confirmed values.
unless(aiWindowResolve(probeBinding['sharedWindowRef]) error("normal nil became unknown"))
''')
    assert rows[0]['code'] == 'context_unavailable'
    assert rows[1]['valid']


def test_unknown_then_confirmed_change_retires_old_reference_once(tmp_path):
    probe(tmp_path, r'''
probeBinding=identityBinding('editor) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef] probeOriginal=getd('geGetWindowCellView)
procedure(geGetWindowCellView(win) error("temporary read"))
sicoContextJson(probeBinding)
when(probeGone error("unknown retired"))
putd('geGetWindowCellView probeOriginal)
probeDisplay=cadr(probeCvs) probeEdit=probeDisplay
sicoContextJson(probeBinding)
unless(and(!probeBinding['valid] length(probeGone)==1) error("confirmed change not retired"))
probeDisplay=car(probeCvs) probeEdit=probeDisplay
sicoContextJson(probeBinding)
when(or(probeBinding['valid] aiWindowResolve(probeRef)) error("old source revived"))
''')


@pytest.mark.parametrize('selection,getter', [
    ('test', 'axlGetTests'), ('history', 'axlGetHistoryEntry'),
    ('history', 'axlGetHistoryName'), ('test', 'axlGetMainSetupDB'),
])
def test_ade_selection_read_failure_recovers_but_removal_never_revives(tmp_path, selection, getter):
    rows = probe(tmp_path, r'''
procedure(axlGetMainSetupDB(session) 'setup)
probeSelectionLive=t
procedure(axlGetTests(setup) list(t when(probeSelectionLive list("dc"))))
procedure(axlGetHistoryEntry(setup name) when(probeSelectionLive 'history0))
procedure(axlGetHistoryName(history) "Interactive.0")
probeBinding=identityBinding('ade)
if("%s"=="history" then
  probeBinding['historyName]="Interactive.0" probeBinding['historyHandle]='history0
else probeBinding['testName]="dc")
sicoContextJson(probeBinding) probeRef=probeBinding['sharedWindowRef]
probeOriginal=getd('%s)
putd('%s lambda((@rest ignored) error("selection temporarily unreadable")))
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
when(or(probeBinding['sourceRetired] probeGone) error("unreadable selection retired"))
putd('%s probeOriginal)
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
probeSelectionLive=nil
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
probeSelectionLive=t
printf("IDENTITY_JSON %%s\n" sicoContextJson(probeBinding))
unless(probeBinding['sharedWindowRef]==probeRef error("selection recovery changed reference"))
''' % (selection, getter, getter, getter))
    assert [r['valid'] for r in rows] == [False, True, False, False]
    assert rows[0]['code'] == 'context_unavailable'
    assert rows[2]['code'] == rows[3]['code'] == 'stale_context'


def test_optional_metadata_error_does_not_make_source_unknown(tmp_path):
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('editor) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef]
procedure(hiGetAppType(win) error("optional app label failed"))
printf("IDENTITY_JSON %s\n" sicoContextJson(probeBinding))
unless(and(aiWindowResolve(probeRef)=='editor !probeGone) error("metadata retired source"))
''')
    assert rows[0]['valid'] is True


def test_captured_ade_temporary_failure_stops_before_circuit_dispatch(tmp_path):
    from sico.transport.circuit import encode_value

    envelope = encode_value([["retained-preparation", "digest", "request"], None, None])
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('ade) sicoContextJson(probeBinding)
procedure(sicoSessionTarget(cid target) probeBinding)
procedure(axlGetSessionCellName(session) error("ADE identity temporarily unreadable"))
procedure(aiCreateExecute(@rest ignored) error("Write must not be entered"))
printf("IDENTITY_JSON %%s\n" sicoCircuitReply(nil list("temporary_ade"
  "circuit_call" "captured" "aiCreateExecute" "%s")))
when(or(probeBinding['sourceRetired] probeGone) error("temporary ADE read retired source"))
''' % envelope)
    assert rows[0]['ok'] is False and rows[0]['code'] == 'context_unavailable'
    diagnostic = rows[0]['diagnostic']
    assert diagnostic['stage'] == 'source_check'
    assert diagnostic['function_entered'] is False
    assert diagnostic['operation_dispatched'] is False


@pytest.mark.parametrize('reader', ['inspect', 'entry', 'list'])
def test_shared_reader_catches_read_failure_without_retiring_source(tmp_path, reader):
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('editor) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef] probeOriginal=getd('geGetWindowCellView)
procedure(geGetWindowCellView(win) error("summary read failed"))
printf("IDENTITY_JSON %%s\n" %s)
when(or(aiWindowEntries[probeRef]['closed] probeGone) error("shared read retired source"))
putd('geGetWindowCellView probeOriginal)
unless(aiWindowResolve(probeRef)=='editor error("shared source did not recover"))
''' % {'inspect': 'aiWindowInspect(probeRef)', 'entry': 'aiEntryContext(probeRef 8 nil)',
       'list': 'aiWindowList(0 8)'}[reader])
    assert rows[0]['ok'] is False
    assert rows[0]['code'] in {'context_unavailable', 'read_failed'}


@pytest.mark.parametrize('reader', ['inspect', 'entry'])
def test_shared_reader_revalidates_after_summary(tmp_path, reader):
    rows = probe(tmp_path, r'''
probeBinding=identityBinding('editor) sicoContextJson(probeBinding)
probeRef=probeBinding['sharedWindowRef] probeReads=0
procedure(geGetWindowCellView(win)
  probeReads=probeReads+1
  when(probeReads>=3 error("read failed during postvalidation")) probeDisplay)
printf("IDENTITY_JSON %%s\n" %s)
when(or(aiWindowEntries[probeRef]['closed] probeGone) error("postvalidation retired source"))
''' % ('aiWindowInspect(probeRef)' if reader == 'inspect' else 'aiEntryContext(probeRef 8 nil)'))
    assert rows[0]['code'] == 'context_unavailable'
