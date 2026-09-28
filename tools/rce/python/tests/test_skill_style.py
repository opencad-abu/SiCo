from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
STYLE_PATH = CAD_ROOT / "utility/skill_style.py"
SPEC = importlib.util.spec_from_file_location("skill_style", STYLE_PATH)
assert SPEC is not None and SPEC.loader is not None
skill_style = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = skill_style
SPEC.loader.exec_module(skill_style)


def test_style_rewriter_preserves_structural_lists_and_strings() -> None:
    source = """\
(defun sample (value)
  (let (text)
    text=(strcat \"(warn 'data)\" value)
    (case value
      (t (list text)))
    '(list (strcat untouched))
    text))
(defmacro layout (name @key (spacing 5))
  `(hiCreateVerticalBoxLayout ,name ?spacing ,spacing))
(defclass SAMPLE ()
  ((title @initform nil)))
(defmethod render ((instance SAMPLE))
  (list instance))
"""

    rewritten = skill_style.rewrite_source(source)

    assert "procedure(sample(value)" in rewritten
    assert "let((text)" in rewritten
    assert 'strcat("(warn \'data)" value)' in rewritten
    assert "case(value" in rewritten
    assert "(t list(text))" in rewritten
    assert "'(list (strcat untouched))" in rewritten
    assert "defmacro(layout (name @key (spacing 5))" in rewritten
    assert "defclass(SAMPLE ()" in rewritten
    assert "((title @initform nil))" in rewritten
    assert "defmethod(render ((instance SAMPLE))" in rewritten
    assert skill_style.analyze(Path("sample.il"), rewritten) == []


def test_style_checker_rejects_wrapped_algebraic_calls() -> None:
    source = 'procedure(sample() (strcat(sprintf(nil "%d" 1))))\n'
    findings = skill_style.analyze(
        Path("sample.il"),
        source,
    )

    assert [(finding.kind, finding.name) for finding in findings] == [
        ("wrapped algebraic call", "strcat")
    ]
    assert skill_style.rewrite_source(source) == (
        'procedure(sample() strcat(sprintf(nil "%d" 1)))\n'
    )


def test_style_rewriter_preserves_block_comments_and_line_numbers() -> None:
    source = '/* (not a call)\n unmatched ( ; " */\n(list/* keep */ value)\n'
    findings = skill_style.analyze(Path("sample.il"), source)
    assert [(item.line, item.name) for item in findings] == [(3, "list")]
    assert skill_style.rewrite_source(source) == (
        '/* (not a call)\n unmatched ( ; " */\nlist(/* keep */ value)\n'
    )


@pytest.mark.parametrize("mapping", ["", "map ", "mapc ", "mapcan ", "mapcar ", "mapcon ", "maplist "])
def test_style_rewriter_preserves_foreach_formals_but_checks_body(mapping) -> None:
    source = f"(foreach {mapping}(x y) xs ys (list x y))"
    rewritten = f"foreach({mapping}(x y) xs ys list(x y))"
    assert skill_style.rewrite_source(source) == rewritten
    assert skill_style.analyze(Path("sample.il"), rewritten) == []


@pytest.mark.parametrize("expression", [
    "value && other", "value || other", "value == other", "value != other",
    "value < other", "value >= other", "value + other", "value - other",
    "value * other", "value / other", "value % other", "value ** 2",
    "value & mask", "value | mask", "value << 2", "value : other",
    "value ~> name", "value -> name", "value [index]",
])
def test_style_rewriter_preserves_grouped_infix_expressions(expression) -> None:
    source = f"when(({expression}) (list value))"
    assert skill_style.rewrite_source(source) == f"when(({expression}) list(value))"


def test_style_rewriter_still_checks_calls_inside_grouped_expressions() -> None:
    source = "when((value && (null other) || fallback) (list value))"
    assert skill_style.rewrite_source(source) == (
        "when((value && null(other) || fallback) list(value))"
    )


def test_tracked_skill_sources_use_cadence_algebraic_syntax() -> None:
    tracked = skill_style._tracked_skill_files()
    findings = [
        finding
        for path in tracked
        for finding in skill_style.analyze(path)
    ]

    assert CAD_ROOT.parent / "deploy/cadAutoLoad.il.src" in tracked
    assert findings == [], "\n".join(finding.render() for finding in findings)
