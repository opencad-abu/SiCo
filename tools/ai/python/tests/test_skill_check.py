from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cadai.skill_check import MAX_DEPTH, MAX_DIAGNOSTICS, check_source


def check(source: str):
    return check_source(source.encode("utf-8"))


@pytest.mark.parametrize(
    "source",
    [
        "procedure(foo(x) let((y) y=list(x) y))",
        "list(table[fun(x)] matrix[a[b]])",
        'list("( [ ) ] ; /* */" "escaped \\" quote" "backslash \\\\")',
        '/* ( [ " ;\n nested-looking /* closes here */ list(1)',
        '; ) ] /* "\nprocedure(foo() t)',
        "; ignored \\\n ) ]\nlist(1)",
        "; ignored \\\\\n ) ]\nlist(1)",
        "; ignored \\\r\n ) ]\r\nlist(1)",
        "list(1 ; continued \\\n ) ]\n 2)",
        'list(\\( \\) \\[ \\] \\" \\; \\/\\*)',
        "list(\\050 \\051 \\133 \\135)",
        "list(1 \\\n 2)",
        '"multiline\n string ( ["',
        "'(a b (c d))",
        "(list 1 2)",
        "procedure(foo(@optional (x list(1))) x)",
    ],
)
def test_lexically_valid_skill(source):
    result = check(source)
    assert result["ok"], result
    assert result["executed"] is False
    assert result["checks"] == {
        "lexical": "passed",
        "definition_policy": "passed",
        "wire_policy": "passed",
        "native_syntax": "not_run",
        "semantics": "not_run",
    }


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("list(1", "unclosed_paren"),
        ("table[key", "unclosed_index"),
        ("list(1))", "unexpected_close"),
        ("list(1]", "super_right_bracket"),
        ("table[fun(x]", "super_right_bracket"),
        ("table[key)", "mismatched_close"),
        ("]", "unexpected_close"),
        ('"unterminated \\"', "unclosed_string"),
        ("/* unfinished", "unclosed_block_comment"),
        ("list(1) */", "unexpected_comment_close"),
        ("list(1) " + chr(92), "dangling_escape"),
        ('"nul\x00"', "nul_character"),
    ],
)
def test_reports_lexical_errors(source, code):
    result = check(source)
    assert not result["ok"]
    assert result["errors"][0]["code"] == code
    assert result["checks"]["definition_policy"] == "not_run"


def test_positions_use_unicode_columns_and_preserve_original_bytes():
    source = '; 中文\r\nlist("电"\r\n  table[fun(x]\r\n'
    result = check(source)
    error = result["errors"][0]
    assert (error["line"], error["column"]) == (3, 14)
    assert (error["opening"]["line"], error["opening"]["column"]) == (3, 12)
    assert error["context"] == "  table[fun(x]"
    assert result["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert result["source_bytes"] == len(source.encode())


def test_unclosed_reports_eof_and_opening_without_guessing_an_edit():
    result = check("procedure(foo()\n  when(t\n    list(1)\n")
    assert [(e["line"], e["opening"]["line"]) for e in result["errors"]] == [(4, 2), (4, 1)]
    assert all("fix" not in error for error in result["errors"])


@pytest.mark.parametrize(
    "name",
    [
        "defun",
        "defunq",
        "defmacro",
        "defsubst",
        "nlambda",
        "mprocedure",
        "nprocedure",
        "defgeneric",
        "defmethod",
        "define_syntax",
        "flet",
        "labels",
        "macrolet",
    ],
)
@pytest.mark.parametrize("notation", ["{}(foo (x) x)", "({} foo (x) x)"])
def test_rejects_alternative_definition_syntax(name, notation):
    result = check(notation.format(name))
    assert result["checks"]["lexical"] == "passed"
    assert result["errors"][0]["code"] == "procedure_required"
    assert name in result["errors"][0]["message"]


@pytest.mark.parametrize(
    "source",
    [
        "lambda((x y) x + y)",
        "(lambda((x y) x + y) 5 6)",
        "mapcar(lambda((x) x * 2) list(1 2))",
        "lambda(lst foreach(mapcar item lst item + 1))",
        "lambda((x @optional (y 1)) x + y)",
        "procedure(foo(x) lambda((y) x + y))",
        "prog((x y) x = 5 y = 10 return(x + y))",
        "prog((x) loop x = x + 1 if(x > 3 return(x)) go(loop) x = 0)",
        "let((x 1 y 2) x + y)",
        "let(((x 1) (y 2)) x + y)",
        "let(acc ((x 1)) x)",
        "let((y) y = list(1) y)",
    ],
)
def test_snippet_forms_are_allowed(source):
    """lambda、prog、let 是执行代码片段的允许形式。"""
    result = check(source)
    assert result["ok"], result
    assert result["checks"]["definition_policy"] == "passed"


@pytest.mark.parametrize(
    "source",
    [
        "(lambda (x y) x + y)",
        "lambda()",
        "lambda(5 x)",
        "lambda('(x) x)",
    ],
)
def test_lambda_requires_algebraic_signature(source):
    result = check(source)
    assert result["checks"]["lexical"] == "passed"
    assert result["errors"][0]["code"] == "invalid_lambda_form"


@pytest.mark.parametrize(
    "source",
    [
        "define((foo x) x)",
        "(define (foo x) x)",
        "define(foo(x) x)",
        "list(nlambda((x) x))",
        "def\\un(foo (x) x)",
        r"def\165n(foo (x) x)",
        "`(item ,nlambda((x) x))",
        "`(item ,@(list nlambda((x) x)))",
        "procedure(foo(@optional (x nlambda((x) x))) x)",
        "let(((x nlambda((x) x))) x)",
        "cond((t defun(foo (x) x)))",
        "case(x (t nlambda((x) x)))",
        "procedure(quote(@optional (x nlambda((x) x))) x)",
        "`(x (quote ,nlambda((x) x)))",
        "`(x ',nlambda((x) x))",
        "quasiquote(nlambda((x) x))",
        "defclass(Example () ((value @initform nlambda((x) x))))",
        "defclass(Example () ((quote @initform nlambda((x) x))))",
    ],
)
def test_definitions_inside_executable_forms_are_checked(source):
    assert any(e["code"] == "procedure_required" for e in check(source)["errors"])


@pytest.mark.parametrize(
    "source",
    [
        '/* defun(foo (x) x) */ "lambda((x) x)" ; (defun foo (x) x)',
        "'defun",
        "'(defun foo (x) x)",
        "'defun(foo (x) x)",
        "quote((lambda (x) x))",
        "(quote (defun foo (x) x))",
        "`(defun foo (x) x)",
        "`(item `(x ,lambda((x) x)))",
        "define(value 3)",
        "list(defun lambda nlambda)",
        "let((defun (lambda 1) nlambda) list(defun lambda nlambda))",
        "procedure(foo(defun lambda @optional (nlambda 1)) list(defun lambda nlambda))",
        "case(x (defun 1) ((lambda nlambda) 2))",
        "defstruct(example defun lambda)",
        "table['defun]",
        "let((lambda defun) lambda=1 defun=2 (lambda + defun))",
        "let((procedure) procedure=3 (procedure * 2))",
        "`(x 'lambda((x) x))",
        "`(x ,'lambda((x) x))",
        "defclass(Example () ((defun @initform list(1)) (lambda @initarg lambda)))",
        "foreach((defun lambda) list(1) list(2) list(defun lambda))",
        "foreach(mapcar (defun lambda) list(1) list(2) list(defun lambda))",
    ],
)
def test_data_and_declarations_are_not_definitions(source):
    result = check(source)
    assert result["ok"], result


@pytest.mark.parametrize(
    "source",
    [
        "(procedure (foo x) x)",
        "procedure(foo (x) x)",
        "procedure()",
    ],
)
def test_procedure_requires_algebraic_signature(source):
    assert check(source)["errors"][0]["code"] == "invalid_procedure_form"


def test_all_lines_are_scanned_and_balanced_logic_is_not_certified():
    assert check("nil\nlist(1)\ndefun(foo (x) x)")["errors"]
    assert check("nil\nlist(1)\n(")["errors"]
    result = check("when(cv list(cv))\ndbSave(cv)")
    assert result["ok"] and result["checks"]["semantics"] == "not_run"


def test_bounded_diagnostics_depth_and_encoding():
    result = check(")" * 1000)
    assert len(result["errors"]) == MAX_DIAGNOSTICS
    assert result["errors_truncated"] is True
    assert len(result["errors"][0]["context"]) <= 160
    assert check("(" * (MAX_DEPTH + 1))["errors"][0]["code"] == "scan_limit"
    assert check_source(b"\xff")["errors"][0]["code"] == "invalid_encoding"


def test_current_ai_skill_sources_are_compatible():
    root = Path(__file__).resolve().parents[2]
    paths = sorted((root / "skill").glob("*.il")) + sorted((root / "skill++").glob("*.ils"))
    assert paths
    for path in paths:
        result = check_source(path.read_bytes(), source_name=str(path))
        assert result["ok"], result
