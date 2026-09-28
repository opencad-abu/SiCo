"""Opt-in source-only Virtuoso probes; no contexts or runtime builds."""

import json
import os
from pathlib import Path

import pytest

from utility.skill_probe_support import run_virtuoso_source

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("CAD_RUN_SKILL_SOURCE_PROBES") != "1",
    reason="set CAD_RUN_SKILL_SOURCE_PROBES=1 for source-only Virtuoso probes",
)


def load(path):
    paths = (["ai/skill/AI_inspect_values.il", "ai/skill/AI_inspect_records.il",
              "ai/skill/AI_inspect_snapshot.il", path]
             if path == "ai/skill/AI_inspect.il" else [path])
    return "\n".join(f"load({json.dumps(str(ROOT / item))})" for item in paths)


def test_normalized_skill_serializers_and_foreach(tmp_path):
    source = "\n".join(load(path) for path in (
        "ai/skill/AI_protocol.il", "ai/skill/AI_pdk_core.il",
        "mtsnl/skill/MTS_defaultsWorker.il",
        "mtsnl/skill/MTS_probeWorker.il",
        "mtsnl/skill/MTS_bindingWorker.il",
        "ai/skill/AI_template_symbol_draw.il",
    ))
    source += r'''
printf("PDK_JSON=%s\n" aiPdkJson(aiPdkArray(list(nil t aiPdkBool(nil) 7 1.5 "a\n\"b"))))
printf("PDK_OBJECT=%s\n" aiPdkJson(aiPdkObj(list(list("key" "value")))))
foreach(value list(nil "text" 7 1.5 'symbol list(1 "two"))
  printf("PDK_RAW=%s\n" aiPdkJson(aiPdkRaw(value))))
printf("MTS_JSON=%s\n" mtsDefaultsJsonValue(list(nil t 7 1.5 "a\n\"b" 'symbol list(2))))
unless(equal(foreach(mapcar (x y) list(1 2) list(3 4) x+y) list(4 6))
  error("multi-variable foreach changed"))
unless(and(isCallable('mtsRuntimeDefaultsMae) isCallable('mtsRuntimeBinding))
  error("worker procedure missing"))
printf("SOURCE_SERIALIZERS_PASS\n")
exit()
'''
    output = run_virtuoso_source(source, tmp_path, log_path=tmp_path / "serializers.log")
    assert "SOURCE_SERIALIZERS_PASS" in output
    values = {}
    for line in output.splitlines():
        line = line.removeprefix("\\o ").removeprefix("> ")
        key, _, value = line.partition("=")
        if key in {"PDK_JSON", "PDK_OBJECT", "PDK_RAW", "MTS_JSON"}:
            values.setdefault(key, []).append(json.loads(value))
    assert values["PDK_JSON"][0] == [None, True, False, 7, 1.5, 'a\n"b']
    assert values["PDK_OBJECT"][0] == {"key": "value"}
    assert values["MTS_JSON"][0] == [None, True, 7, 1.5, 'a\n"b', "symbol", [2]]
    raw = values["PDK_RAW"][:6]
    assert [item["type"] for item in raw] == ["nil", "string", "integer", "float", "symbol", "list"]
    assert all(item["status"] == "known" for item in raw)
    assert raw[-1]["value"][1]["value"] == "two"


@pytest.mark.parametrize("path,version,revision,functions", [
    ("ai/skill/AI_inspect.il", "aiInspectVersion", "aiInspectRevision",
     ["aiInspectMaxItems", "aiInspectLayoutShapeJson", "aiSnapshotSchematic"]),
    ("sico/skill/SICO_menus.il", "sicoMenusVersion", "sicoMenusRevision",
     ["sicoWindowForSession", "sicoStartAdeMenuPoll"]),
])
def test_local_version_guard_preserves_definitions_and_reloads_stale_source(
    tmp_path, path, version, revision, functions,
):
    source = [load(path), 'procedure(auditSentinel(@rest args) "sentinel")']
    for name in functions:
        source.extend([
            f"unless(isCallable('{name}) error(\"cold load omitted {name}\"))",
            f"putd('{name} getd('auditSentinel))",
        ])
    source.append(load(path))
    for name in functions:
        source.append(f"unless(eq(getd('{name}) getd('auditSentinel)) error(\"same version reloaded {name}\"))")
    source.extend([f'{version}="stale"', load(path)])
    for name in functions:
        source.append(f"when(eq(getd('{name}) getd('auditSentinel)) error(\"stale version retained {name}\"))")
    source.extend([
        f'unless({version}=={revision}() error("revision mismatch"))',
        'printf("SOURCE_RELOAD_PASS\\n")', "exit()",
    ])
    output = run_virtuoso_source("\n".join(source), tmp_path, log_path=tmp_path / "reload.log")
    assert "SOURCE_RELOAD_PASS" in output


def test_circuit_window_raise_restores_and_returns_the_window(tmp_path):
    library = tmp_path / "probeWindowLibrary"
    source = "\n".join([
        load("tools/ai/skill/AI_circuit.il"),
        "unless(isCallable('aiCircuitRaiseWindow) error(\"raise helper missing\"))",
        'unless(null(aiCircuitRaiseWindow(nil)) error("nil window must stay a no-op"))',
        f'ddCreateLib("probeWindowLib" {json.dumps(str(library))})',
        'probeWindow=deNewCellView("probeWindowLib" "cellA" "schematic" "schematic" nil)',
        'unless(probeWindow error("probe window unavailable"))',
        'unless(equal(aiCircuitRaiseWindow(probeWindow) probeWindow) '
        'error("helper must return its window"))',
        'unless(eq(hiGetWindowState(probeWindow) \'mapped) error("fresh window is not mapped"))',
        "hiIconifyWindow(probeWindow)",
        "unless(eq(hiGetWindowState(probeWindow) 'iconified) "
        'error("probe window did not iconify"))',
        "aiCircuitRaiseWindow(probeWindow)",
        "unless(eq(hiGetWindowState(probeWindow) 'mapped) "
        'error("iconified window must be restored and raised"))',
        'printf("CIRCUIT_WINDOW_RAISE_PASS\\n")',
        "exit()",
    ])
    output = run_virtuoso_source(source, tmp_path, log_path=tmp_path / "window-raise.log")
    assert "CIRCUIT_WINDOW_RAISE_PASS" in output
