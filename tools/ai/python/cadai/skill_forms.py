"""Shared traversal of explicit SKILL calls in a delimiter tree; not an evaluator."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from .skill_lexer import Token


@dataclass
class Node:
    token: Token
    prefixes: tuple[str, ...] = ()
    operator: Token | None = None
    children: list[Node] = field(default_factory=list)


def executable_calls(roots: list[Node]) -> Iterator[tuple[Node, Token, list[Node]]]:
    """Visit explicit executable calls without treating declarations or quoted data as calls."""
    pending = [(node, 0, "exec") for node in reversed(roots)]
    while pending:
        node, quasiquote, role = pending.pop()
        if role == "data":
            continue
        quoted = False
        for prefix in node.prefixes:
            if prefix == "'" and not quasiquote:
                quoted = True
                break
            elif prefix == "`":
                quasiquote += 1
            elif prefix in {",", ",@"} and quasiquote:
                quasiquote -= 1
                role = "exec"
        if quoted or node.token.kind != "open":
            continue
        arguments = node.children
        operator = node.operator
        if (
            operator is None
            and role == "exec"
            and node.token.value == "("
            and arguments
            and arguments[0].token.kind == "name"
            and not arguments[0].prefixes
            and not (
                len(arguments) > 1
                and (
                    (
                        arguments[1].token.kind == "other"
                        and arguments[1].token.value in "+-*/%<>=!&|^:~"
                    )
                    or arguments[1].token.value == "["
                )
            )
        ):
            operator = arguments[0].token
            arguments = arguments[1:]
        name = operator.value if operator and role == "exec" else ""
        # Under backquote, quote is template data and does not suppress an
        # unquote below it. Outside backquote its whole argument is opaque.
        if name == "quote" and not quasiquote:
            continue
        if not quasiquote and role == "exec":
            if operator is not None:
                yield node, operator, arguments
        child_roles = ["exec"] * len(arguments)
        if not quasiquote:
            if role in {"bindings", "signature"}:
                child_roles = [
                    "binding" if child.token.kind == "open" else "data" for child in arguments
                ]
            elif role in {"binding", "case_clause"} and arguments:
                child_roles[0] = "data"
            elif role == "class_slots":
                child_roles = ["class_slot"] * len(arguments)
            elif role == "class_slot":
                active = False
                for index, child in enumerate(arguments):
                    if child.token.kind == "name" and child.token.value.startswith("@"):
                        active = child.token.value == "@initform"
                        child_roles[index] = "data"
                    else:
                        child_roles[index] = "exec" if active else "data"
            elif name in {"let", "letseq", "letrec", "prog"} and arguments:
                child_roles[0] = "bindings"
            elif name == "procedure" and arguments:
                child_roles[0] = "signature"
            elif name == "case":
                child_roles[1:] = ["case_clause"] * max(0, len(arguments) - 1)
            elif name == "cond":
                child_roles = ["sequence"] * len(arguments)
            elif name == "defclass":
                child_roles = ["data"] * len(arguments)
                if len(arguments) >= 3:
                    child_roles[2] = "class_slots"
            elif name in {"defstruct", "declare"}:
                child_roles = ["data"] * len(arguments)
            elif name in {"foreach", "foreachs"} and arguments:
                binding_index = int(
                    arguments[0].token.value
                    in {
                        "map",
                        "mapc",
                        "mapcan",
                        "mapcar",
                        "mapcon",
                        "maplist",
                    }
                )
                child_roles[: binding_index + 1] = ["data"] * min(len(arguments), binding_index + 1)
        pending.extend(
            (child, quasiquote, child_role)
            for child, child_role in reversed(list(zip(arguments, child_roles)))
        )
