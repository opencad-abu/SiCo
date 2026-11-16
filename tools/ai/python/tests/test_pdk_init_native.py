"""DD-selected initialization, failure boundaries and callback discovery in Virtuoso."""

import json

from test_circuit_library_native import ROOT, native


def test_selected_init_cache_failures_and_builtin_discovery(tmp_path):
    initializer = (
        "initProbeCount=if(boundp('initProbeCount) initProbeCount+1 1)\n"
        'procedure(initProbeCallback() error("CALLBACK_EXECUTED_DURING_DISCOVERY"))\nt\n'
    )
    broken = 'error("intentional init failure")\n'
    (tmp_path / "rebound").mkdir()
    (tmp_path / "rebound.lib").write_text(f"DEFINE InitProbe {tmp_path}/rebound\n")
    native(tmp_path, [
        'procedure(initTestWrite(name value) '
        'aiWriteTextFile(strcat(name ".json") aiPdkJson(value)))',
        f'load({json.dumps(str(ROOT / "skill/AI_cdf_callbacks.il"))})',
        'foreach(name list("InitProbe" "MissingInit" "BrokenInit" "basic") '
        'ddCreateLib(name strcat(getWorkingDir() "/" name)))',
        f'aiWriteTextFile("InitProbe/libInit.il" {json.dumps(initializer)})',
        f'aiWriteTextFile("BrokenInit/libInit.il" {json.dumps(broken)})',
        'initTestWrite("missing_library" aiPdkInitLibrary("NoSuchLibrary"))',
        'initTestWrite("missing_init" aiPdkInitLibrary("MissingInit"))',
        'initTestWrite("fake_basic" aiPdkInitLibrary("basic"))',
        'initTestWrite("broken" aiPdkInitLibrary("BrokenInit"))',
        'when(aiPdkInitState["BrokenInit"] error("Failed init was cached"))',
        'aiWriteTextFile("initialized.json" aiPdkInitializeLibrary("InitProbe"))',
        'unless(isCallable(\'initProbeCallback) error("Initializer was not loaded"))',
        'initCount=initProbeCount',
        'aiWriteTextFile("cached.json" aiPdkInitializeLibrary("InitProbe"))',
        'unless(initCount==initProbeCount error("Cached initialization repeated"))',
        'foreach(cell list("active" "plain") '
        'v=dbOpenCellViewByType("InitProbe" cell "symbol" "schematicSymbol" "a") '
        'dbCreateRect(v list("device" "drawing") list(0:0 1:1)) dbSave(v) dbClose(v) '
        'c=cdfCreateBaseCellCDF(ddGetObj("InitProbe" cell)) '
        'cdfCreateParam(c ?name "w" ?prompt "Width" ?type "string" ?defValue "1u" '
        '?callback if(cell=="active" "initProbeCallback()" "nil")) '
        'when(cell=="plain" c->formInitProc="" c->doneProc="") cdfSaveCDF(c))',
        'foreach(cell list("active" "plain") aiWriteTextFile(strcat(cell ".json") '
        'aiCrListExtensions("InitProbe" cell "symbol" ddGetObjReadPath(ddGetObj("InitProbe")))))',
        'aiWriteTextFile("no_support.json" aiCrListExtensions("MissingInit" "m" "symbol" '
        'ddGetObjReadPath(ddGetObj("MissingInit"))))',
        # Rebinding a previously initialized name cannot reuse the old success.
        'ddSetForcedLib(strcat(getWorkingDir() "/rebound.lib")) ddUpdateLibList()',
        'initTestWrite("rebound" aiPdkInitLibrary("InitProbe"))',
    ], virtuoso=True)
    def read(name):
        return json.loads((tmp_path / (name + ".json")).read_text())
    for name, code in [
        ("missing_library", "pdk_init_library_unavailable"),
        ("missing_init", "pdk_init_missing"), ("fake_basic", "pdk_init_missing"),
        ("broken", "pdk_init_failed"), ("rebound", "pdk_init_missing"),
    ]:
        assert read(name)[:2] == [None, code], read(name)
    initialized = read("initialized")
    assert initialized["ok"] and initialized["initialized"] and not initialized["cached"]
    assert initialized["init_path"] == str(tmp_path / "InitProbe/libInit.il")
    assert read("cached")["cached"] is True
    assert read("active")["items"] == [
        ["builtin:cdf-callbacks:v1", "20261022.ai.cdf.callbacks.v5", True]
    ]
    assert not read("plain")["items"]
    assert "no CDF callbacks" in read("plain")["builtin_diagnostic"]
    assert not read("no_support")["items"]
    assert "pdk_init_missing" in read("no_support")["builtin_diagnostic"]
    assert read("active")["callbacks_executed"] is False
