"""Native procedure arity: one agreement across the wire and the skill tree.

Fake clients never exercise the transport arity gate, so drift there stays
invisible until a real session calls the procedure (this is how
``aiTemplateSymbolPreview`` and the circuit edit pair were dead on the wire
while their unit tests stayed green).  These tests pin three things:

1. the Python transport table and the SKILL dispatch table are identical;
2. every procedure parsed from ``tools/ai/skill`` matches its table bounds,
   except the explicitly listed entries whose spare optional arguments are
   not wired yet (they may only be narrower, and that has to be conscious);
3. the argument lists the real callers build pass ``literal_call`` and
   ``wire_arguments`` unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path

from sico.transport.circuit import ARITIES, literal_call, wire_arguments

ROOT = Path(__file__).resolve().parents[4]
SKILL = ROOT / "tools/ai/skill"
TRANSPORT = ROOT / "tools/sico/python/sico/transport/circuit.py"

TABLE_ENTRY = re.compile(r'list\("(\w+)" \'(\w+) (\d+) (\d+)\)')
PROCEDURE = re.compile(r"procedure\(\s*(ai\w+)\s*\(([^)]*)\)")

# Procedures whose extra optional arguments exist in SKILL but are not sent
# through the wire yet; their tables may be narrower than the signature.
UNWIRED_OPTIONALS = frozenset({"aiConfigCreate", "aiWaveCapture"})

# Functions that intentionally stay on the native port path. The first group
# has no captureable native output; the second owns ADE/RDB, GUI or arbitrary
# PDK libInit lifetime. Keep this boundary explicit when adding a dispatch row.
CAPTURE_NATIVE = frozenset({
    "aiCircuitBeginTask", "aiSimModels", "aiCdfRead", "aiCdfDrop",
    "aiResultsPage", "aiResultsRelease", "aiWavePage", "aiWaveRelease",
    "aiSimCreate", "aiSimInspect", "aiSimRun", "aiSimStatus", "aiSimStop",
    "aiCopilotBindResults", "aiResultsCapture", "aiWaveCapture", "aiAcCapture",
    "aiCopilotOpenTarget", "aiPdkInitializeLibrary",
})


def skill_table():
    text = (SKILL / "AI_copilot_circuit.il").read_text(encoding="utf-8")
    table = {}
    for name, spelling, low, high in TABLE_ENTRY.findall(text):
        assert name == spelling
        table[name] = (int(low), int(high))
    return table


def procedure_signatures():
    found = {}
    for path in sorted(SKILL.rglob("*.il")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, params in PROCEDURE.findall(text):
            found.setdefault(name, []).append((path, params))
    return found


def counts(params):
    parts = params.replace("\n", " ").split()

    def index(mark):
        return parts.index(mark) if mark in parts else None

    optional, rest = index("@optional"), index("@rest")
    cut = min([i for i in (optional, rest) if i is not None], default=len(parts))
    required = len(parts[:cut])
    extra = 0
    if optional is not None:
        end = rest if rest is not None and rest > optional else len(parts)
        extra = len([p for p in parts[optional + 1:end] if not p.startswith("@")])
    return required, extra, rest is not None


def test_python_and_skill_arity_tables_are_identical():
    table = skill_table()
    assert table == ARITIES


def test_capture_boundary_is_explicit_for_every_dispatch_row():
    text = (SKILL / "AI_copilot_circuit.il").read_text(encoding="utf-8")
    start = text.index("procedure(aiCopilotSynchronousCaptureP")
    end = text.index("procedure(aiCopilotCaptureRevision", start)
    captured = set(re.findall(r'"(ai\w+)"', text[start:end]))
    table = set(skill_table())
    assert captured <= table
    assert table - captured == CAPTURE_NATIVE


def test_procedure_signatures_match_the_table_bounds():
    for name, rows in procedure_signatures().items():
        if name not in ARITIES:
            continue
        low, high = ARITIES[name]
        for path, params in rows:
            required, extra, rest = counts(params)
            where = f"{name} in {path.name}"
            if rest:
                assert low <= required <= high, where
            elif name in UNWIRED_OPTIONALS:
                assert low <= required and required <= high < required + extra, where
            else:
                assert (low, high) == (required, required + extra), where


def test_template_symbol_preview_accepts_the_caller_argument_lists():
    from cadai.template_symbol import skill_literal

    base = ["task:t", "request:1", "ctpl_x", "digest", "SicoTest", "inv_p1",
            "SicoTest", "/pdk/lib", [["MN", "D", "Y"]], 40, 30]
    for style, slot in (("rectangular_module", None),
                        ("template_outline", ["body", "stubs", "sel", "boot", "labels"])):
        values = [*base, slot, "inv_p1", style]
        code = "aiTemplateSymbolPreview(" + " ".join(skill_literal(v) for v in values) + ")"
        function, parsed = literal_call(code)
        assert function == "aiTemplateSymbolPreview"
        assert len(parsed) == 14, style
        wire_arguments({"function": function, "values": parsed, "claim": None, "expected": None})


def test_pdk_initialize_passes_the_arity_gate():
    function, parsed = literal_call('aiPdkInitializeLibrary("smic28hkmg")')
    assert (function, parsed) == ("aiPdkInitializeLibrary", ["smic28hkmg"])
    wire_arguments({"function": function, "values": parsed, "claim": None, "expected": None})


def test_circuit_edit_prepare_and_execute_pass_the_arity_gate():
    from cadai.circuit_create import skill_literal

    payload = [["instances", [["MN", "n25_ckt", "R0"]]], ["nets", [["A", "G"]]]]
    edit_plan = [["mode", "rebuild"], ["approval", "review-digest"], ["drops", []],
                 ["delete", [["instances", []], ["ports", []], ["nets", []], ["wires", None]]]]
    prepare = ["task:t", "request:1", "plan-digest", payload,
               None, ["SicoTest/inv_p1/schematic", "edit"], edit_plan]
    execute = ["prepare:1", "plan-digest", "request:2", None, "approval-digest"]
    claim = ["session:t", "edit-key", "digest:1"]
    expected = ["task:t", "background", ["SicoTest", "inv_p1", "schematic"], "schematic",
                "", "/workarea/projs/proj4/xh/SicoTest", None]
    for function, values, binding in (("aiCreatePrepare", prepare, (None, None)),
                                      ("aiCreateExecute", execute, (claim, expected))):
        code = function + "(" + " ".join(skill_literal(v) for v in values) + ")"
        parsed_function, parsed = literal_call(code)
        assert parsed_function == function
        wire_arguments({"function": function, "values": parsed,
                        "claim": binding[0], "expected": binding[1]})


def test_library_prepare_real_caller_passes_the_arity_gate(tmp_path, monkeypatch):
    """The reattach flag travels through the real caller and the wire gate."""
    from cadai import circuit_library

    captured = []
    monkeypatch.setattr(circuit_library, "call_skill",
                        lambda client, code, native=False: captured.append(code) or {"ok": True})
    circuit_library.call_library(
        "prepare_circuit_library",
        dict(task_ref="task:t", request_id="request1", library="SicoTest",
             technology_library="smic28hkmg", reattach_technology=True),
        client=None, workspace=str(tmp_path))
    circuit_library.call_library(
        "prepare_circuit_library",
        dict(task_ref="task:t", request_id="request2",
             technology_library="smic28hkmg"),
        client=None, workspace=str(tmp_path))
    assert len(captured) == 2
    for index, expected_tail in ((0, "t)"), (1, "nil)")):
        function, parsed = literal_call(captured[index])
        assert function == "aiLibraryPrepare"
        assert len(parsed) == 6, captured[index]
        assert captured[index].rstrip().endswith(expected_tail), captured[index]
        wire_arguments({"function": function, "values": parsed, "claim": None, "expected": None})
