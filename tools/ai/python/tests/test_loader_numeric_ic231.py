"""Loaded shared helpers preserve numeric semantics without SKILL parser warnings."""

import json

import pytest

from test_assistant_loader_ic231 import DB, TOOLS, run_probe

pytestmark = pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")


def test_simulation_options_compare_only_complete_numeric_strings(tmp_path):
    output = run_probe(tmp_path, [
        f'load({json.dumps(str(TOOLS / "ai/skill++/AI_shared.ils"))})',
        'foreach(pair list(list("1.0" "1") list("1e3" "1000.0")'
        ' list(" +01.00 " "1") list("-1.0" "-1") list("-0.0" "0"))'
        ' unless(aiSimOptionEqual(car(pair) cadr(pair)) error("Equivalent numeric option rejected")))',
        'foreach(pair list(list("1x0" "1") list("1.0V" "1") list("1 0" "1")'
        ' list("1.2" "1.3") list("NaN" "1") list("" "0") list("1e" "1"))'
        ' when(aiSimOptionEqual(car(pair) cadr(pair)) error("Invalid/unequal numeric option accepted")))',
        'unless(aiSimOptionEqual("conservative" "conservative") error("Equal text rejected"))',
    ])
    assert "*WARNING*" not in output


def test_native_byte_budget_is_exact_at_2gib_and_does_not_wrap(tmp_path):
    (tmp_path / "cds.lib").write_text("")
    output = run_probe(tmp_path, [
        f'load({json.dumps(str(TOOLS / "ai/skill++/AI_shared.ils"))})',
        'aiTestWorkspaceLibrary()',
        'budgetCv=dbOpenCellViewByType("SicoTest" "byte_budget" "layout" "maskLayout" "a")',
        'unless(budgetCv error("Auxiliary background view unavailable"))',
        'dbCreateRect(budgetCv list("device" "drawing") list(0:0 1:1))',
        'dbSave(budgetCv) dbClose(budgetCv)',
        'budgetCv=dbOpenCellViewByType("SicoTest" "byte_budget" "layout" "" "r")',
        'budgetTotal=0.0',
        'foreach(file ddGetObjFilesEx(ddGetObj("SicoTest" "byte_budget" "layout") 2)'
        ' budgetTotal=budgetTotal+float(fileLength(ddGetObjReadPath(file))))',
        'unless(budgetTotal>1 error("Fixture lacks native bytes"))',
        'budget=makeTable("budget" 0) budget[\'bytes]=2147483648.0-budgetTotal',
        'aiTplNativeView(budgetCv "source" budget)',
        'unless(budget[\'bytes]==2147483648.0 error("Exact 2 GiB budget changed"))',
        'budget=makeTable("budget" 0) budget[\'bytes]=2147483648.0-budgetTotal+1',
        'when(errset(aiTplNativeView(budgetCv "source" budget) nil) error("Over budget accepted"))',
        'budget=makeTable("budget" 0) budget[\'bytes]=2147483647',
        'when(errset(aiTplNativeView(budgetCv "source" budget) nil) error("Fixnum overflow bypassed budget"))',
        'unless(budget[\'bytes]>2147483648.0 error("Byte counter wrapped"))',
        'dbClose(budgetCv)',
    ])
    assert "*WARNING*" not in output
