"""Freeze real DR vectors, checking page values, native types and invalid axes."""

import json

import pytest

from utility.tests.skill_regression.native import definitions, run


def support():
    return definitions("ai_shared") + r'''
procedure(auditWaveWrite(auditName auditValue)
 let((auditPort) auditPort=outfile(strcat(auditName ".json"))
 fprintf(auditPort "%s" aiCircuitJson(auditValue)) close(auditPort)))
procedure(auditWave(auditX auditY auditType)
 drCreateWaveform(drCreateVec('double auditX) drCreateVec(auditType auditY)))
'''


@pytest.mark.parametrize("ac", [False, True])
def test_real_wave_pages_are_frozen_and_units_and_samples_preserved(tmp_path, ac):
    freeze = "aiAcFreeze" if ac else "aiWaveFreeze"
    run(tmp_path, support() + f'''
auditX=nil auditY=nil
for(auditIndex 0 512 auditX=cons(auditIndex auditX) auditY=cons(auditIndex*2 auditY))
auditWaveform=auditWave(reverse(auditX) reverse(auditY) 'double)
drGetWaveformXVec(auditWaveform)->units="{'Hz' if ac else 's'}"
drGetWaveformYVec(auditWaveform)->units="V"
auditFrozen={freeze}(auditWaveform)
unless(and(car(auditFrozen)=="waveform" cadr(auditFrozen)==513 length(caddr(auditFrozen))==3) error("Wave page counts"))
drSetElem(drGetWaveformYVec(auditWaveform) 0 99.0)
auditWaveWrite("frozen" auditFrozen)
aiWaveCaptures["samples"]=list(caddr(auditFrozen))
auditPort=outfile("page.json") fprintf(auditPort "%s" aiWavePage("samples" 2)) close(auditPort)
foreach(auditBad list(-1 3 1.5 "1")
 unless(rexMatchp("waveform_page_missing" aiWavePage("samples" auditBad)) error("Bad wave page accepted")))
unless(rexMatchp("waveform_page_missing" aiWavePage("absent" 0)) error("Absent capture read"))
unless(aiWaveRelease("samples")=="{{\\\"ok\\\":true}}" error("Release reply"))
unless(and(length(aiWaveCaptures)==0 aiWaveRelease("samples")) error("Release not idempotent"))
''', engine="virtuoso")
    frozen = json.loads((tmp_path / "frozen.json").read_text())
    pages = [json.loads(page) for page in frozen[2]]
    assert [len(page) for page in pages] == [256, 256, 1]
    assert [row for page in pages for row in page] == [
        [i, i * 2, 0] if ac else [i, i * 2] for i in range(513)]
    assert frozen[3:5] == ["Hz" if ac else "s", "V"]
    if ac:
        assert frozen[5] == "double"
    assert json.loads((tmp_path / "page.json").read_text()) == {"ok": True, "samples": pages[2]}


def test_ac_complex_phasors_and_native_wave_classification(tmp_path):
    run(tmp_path, support() + r'''
auditComplex=auditWave(list(0 1 2) list(complex(1 2) complex(-1 0) complex(0 -3)) 'doublecomplex)
auditWaveWrite("complex" aiAcFreeze(auditComplex))
unless(car(aiWaveFreeze(auditComplex))=="complex" error("Complex transient coerced"))
auditPlain=auditWave(list(0 1) list(2 3) 'double)
foreach(auditFreeze list('aiWaveFreeze 'aiAcFreeze)
 unless(car(apply(auditFreeze list(nil)))=="missing" error("Missing classification"))
 unless(car(apply(auditFreeze list(7)))=="scalar" error("Scalar classification"))
 unless(car(apply(auditFreeze list("bad")))=="error" error("Unsupported value classification"))
 unless(car(apply(auditFreeze list(famCreateFamily("sweep" 'double))))=="error" error("Empty family classification"))
 unless(car(apply(auditFreeze list(famAddValue(famCreateFamily("sweep" 'double) 1.0 auditPlain))))=="family" error("Family flattened"))
 unless(car(apply(auditFreeze list(auditWave(list(0 1) list("a" "b") 'string))))=="error" error("String waveform coerced")))
unless(and(aiAcFinite(0) aiAcFinite(-7.5) !aiAcFinite(1e300) !aiAcFinite("1")) error("Finite classification"))
unless(rexMatchp("ac_analysis_required" aiAcCapture("ref" "h" "t" "c" 0 "tran" "/out" "id")) error("Non-AC capture accepted"))
''', engine="virtuoso")
    frozen = json.loads((tmp_path / "complex.json").read_text())
    assert frozen[:2] == ["waveform", 3] and frozen[5] == "doublecomplex"
    assert json.loads(frozen[2][0]) == [[0, 1, 2], [1, -1, 0], [2, 0, -3]]


def test_wave_capture_failure_keeps_the_native_reason(tmp_path):
    run(tmp_path, support() + r'''
auditCapture=aiWaveCapture("absent" "Interactive.0" "ADC_TT" "tt" 1 "tran" "/VCLK" "wave-id")
unless(rexMatchp("waveform_capture_failed" auditCapture) error("Capture failure code lost"))
unless(rexMatchp("Exact history|context|session|nil" auditCapture) error("Capture failure reason lost"))
''', engine="virtuoso")


@pytest.mark.parametrize("ac", [False, True])
def test_wave_freeze_rejects_bad_axes_nonfinite_values_and_excess_samples(tmp_path, ac):
    freeze = "aiAcFreeze" if ac else "aiWaveFreeze"
    negative_check = ('''unless(null(errset(aiAcFreeze(auditWave(list(-1 0) list(1 2) 'double)) nil))
 error("Negative AC frequency accepted"))''' if ac else "")
    run(tmp_path, support() + f'''
; DR itself rejects mismatched vector lengths and returns nil; nil has the
; documented missing status, so it is not evidence of an accepted bad wave.
unless(car({freeze}(auditWave(list(0 1) list(1) 'double)))=="missing" error("Rejected native construction status"))
foreach(auditBad list(auditWave(list(0 0) list(1 2) 'double)
 auditWave(list(1 0) list(1 2) 'double)
 auditWave(list(0 1) list(1 1e300) 'double))
 unless(null(errset({freeze}(auditBad) nil)) error("Invalid wave accepted")))
{negative_check}
auditData=nil for(auditIndex 0 65536 auditData=cons(auditIndex auditData))
auditData=reverse(auditData)
unless(null(errset({freeze}(auditWave(auditData auditData 'double)) nil)) error("Oversize wave truncated or accepted"))
''', engine="virtuoso")


def test_result_values_outputs_and_frozen_page_lifecycle(tmp_path):
    run(tmp_path, support() + r'''
defstruct(auditResultOutput value testName cornerName pointID name)
auditOutput=make_auditResultOutput(?testName "tb" ?cornerName "corner" ?pointID 3 ?name "gain")
foreach(auditSpec list(list(nil "missing") list('Error "error") list(3.5 "scalar") list("vector" "non_scalar"))
 auditOutput->value=car(auditSpec)
 auditRow=aiResultsOutput(auditOutput)
 unless(equal(auditRow list("output" "tb" "corner" 3 "gain" cadr(auditSpec)
  if(cadr(auditSpec)=="scalar" 3.5 nil))) error("Output coordinate/status changed")))
unless(equal(aiResultsValue(list(1 "text" 'symbol nil) 0) list(1 "text" "symbol" nil)) error("Typed result values"))
auditWide=nil for(auditIndex 1 129 auditWide=cons(1 auditWide))
auditLong="x" for(auditIndex 1 13 auditLong=strcat(auditLong auditLong))
foreach(auditBad list(auditWide auditLong 1e300 makeTable("opaque" nil))
 unless(null(errset(aiResultsValue(auditBad 0) nil)) error("Unbounded value accepted")))
unless(null(errset(aiResultsValue(nil 9) nil)) error("Nesting limit ignored"))
aiResultsCaptures["result"]=list("[[1,2]]" "[[3,4]]")
auditPort=outfile("result.json") fprintf(auditPort "%s" aiResultsPage("result" 1)) close(auditPort)
foreach(auditBad list(-1 2 1.5 "1")
 unless(rexMatchp("capture_page_missing" aiResultsPage("result" auditBad)) error("Invalid result page accepted")))
unless(rexMatchp("capture_page_missing" aiResultsPage("absent" 0)) error("Missing result read"))
unless(aiResultsRelease("result")=="{\"ok\":true}" error("Result release reply"))
unless(and(length(aiResultsCaptures)==0 aiResultsRelease("result")) error("Result release retained table key"))
''', engine="virtuoso")
    assert json.loads((tmp_path / "result.json").read_text()) == {"ok": True, "records": [[3, 4]]}
