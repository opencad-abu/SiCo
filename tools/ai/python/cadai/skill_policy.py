"""Conservative checks of explicit function definitions in a delimiter tree.

This is not a SKILL evaluator or a complete parser. Quoted data and strings are
opaque; runtime eval, macro expansion and nested file loads are not followed.
Named functions must use procedure(name(args) ...); inline snippets may use
lambda((args) ...), prog((vars) ...) or let((bindings) ...).
"""

from __future__ import annotations

from collections.abc import Iterator

from .skill_forms import Node, executable_calls
from .skill_lexer import Token

ALTERNATIVE_DEFINITIONS = frozenset(
    {
        "defun",
        "defunq",
        "defmacro",
        "defsubst",
        "nlambda",
        "nprocedure",
        "mprocedure",
        "defgeneric",
        "defmethod",
        "define_syntax",
        "flet",
        "labels",
        "macrolet",
    }
)
# 允许的内联代码片段包装：lambda 匿名函数，prog/let 承载局部绑定与顺序语句。
SNIPPET_FORMS_TEXT = "lambda((args) ...), prog((vars) ...) or let((bindings) ...)"


def definition_errors(roots: list[Node]) -> Iterator[tuple[str, str, Token]]:
    """Check definitions in executable forms using the common traversal."""
    for node, operator, arguments in executable_calls(roots):
        name = operator.value
        shorthand_define = name == "define" and arguments and arguments[0].token.kind == "open"
        if name in ALTERNATIVE_DEFINITIONS or shorthand_define:
            yield (
                "procedure_required",
                "Function definitions must use procedure(name(args) ...); executable "
                f"snippets may use {SNIPPET_FORMS_TEXT}. {name} is not permitted; rewrite "
                "it as a named procedure or a permitted snippet.",
                operator,
            )
        elif name == "procedure":
            signature = arguments[0] if arguments else None
            if node.operator is None or (
                signature is None
                or signature.token.value != "("
                or signature.operator is None
                or signature.prefixes
            ):
                yield (
                    "invalid_procedure_form",
                    "Use procedure(name(args) body ...), with no space before either '('.",
                    operator,
                )
        elif name == "lambda":
            # 匿名函数与 procedure 同源：同样要求代数写法与非引用形参。
            signature = arguments[0] if arguments else None
            usable = signature is not None and not signature.prefixes and (
                signature.token.kind == "name"
                or (signature.token.value == "(" and signature.operator is None)
            )
            if node.operator is None or not usable:
                yield (
                    "invalid_lambda_form",
                    "Use lambda((args ...) body ...) or lambda(argList body ...), "
                    "with no space before '('.",
                    operator,
                )
