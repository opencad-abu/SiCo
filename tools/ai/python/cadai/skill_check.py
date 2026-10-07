"""Bounded SKILL lexical, definition and explicit wire-call policy preflight."""

from __future__ import annotations

import hashlib
from typing import Any

from .runtime import MAX_SPOOL_BYTES
from .skill_forms import Node
from .skill_lexer import Token, tokens
from .skill_policy import definition_errors
from .skill_wire_policy import wire_errors
from .socket_server import RequestFailure

MAX_DIAGNOSTICS = 40
MAX_DEPTH = 512
MAX_TOKENS = 200_000
LEXICAL_MESSAGES = {
    "unclosed_block_comment": "Block comment starting here has no closing '*/'.",
    "unexpected_comment_close": "Unexpected '*/' outside a block comment.",
    "unclosed_string": "String starting here has no closing double quote.",
    "dangling_escape": "Backslash at end of source has no following character.",
}


def check_source(source: bytes, *, source_name: str = "<code>") -> dict[str, Any]:
    """Check exactly these bytes, without rewriting, evaluating or loading them."""
    result: dict[str, Any] = {
        "ok": False,
        "phase": "preflight",
        "executed": False,
        "source": source_name,
        "source_bytes": len(source),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "checks": {
            "lexical": "not_run",
            "definition_policy": "not_run",
            "wire_policy": "not_run",
            "native_syntax": "not_run",
            "semantics": "not_run",
        },
        "errors": [],
        "errors_truncated": False,
    }
    errors = result["errors"]
    checks = result["checks"]
    if len(source) > MAX_SPOOL_BYTES:
        errors.append({"code": "source_too_large", "message": "Source exceeds 8 MiB."})
        return result
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(
            {
                "code": "invalid_encoding",
                "message": "SKILL source must be UTF-8.",
                "byte_offset": exc.start,
            }
        )
        checks["lexical"] = "failed"
        return result

    def location(token: Token) -> dict[str, Any]:
        # Context is bounded, including for a very long single-line expression.
        begin = max(token.start - token.column + 1, token.start - 80)
        context = text[begin : begin + 160].split("\n", 1)[0].split("\r", 1)[0]
        return {
            "line": token.line,
            "column": token.column,
            "offset": token.start,
            "context": context,
            "context_column": token.column - (token.start - begin),
        }

    def add(code: str, message: str, token: Token, opening: Token | None = None) -> None:
        if len(errors) >= MAX_DIAGNOSTICS:
            result["errors_truncated"] = True
            return
        item = {"code": code, "message": message, **location(token)}
        if opening is not None:
            item["opening"] = location(opening)
        errors.append(item)

    nul = text.find("\x00")
    if nul >= 0:
        before = text[:nul]
        line = 1 + before.count("\n") + before.count("\r") - before.count("\r\n")
        column = nul - max(before.rfind("\n"), before.rfind("\r"))
        add(
            "nul_character",
            "NUL can truncate Cadence input and is not permitted.",
            Token("error", "", nul, nul + 1, line, column),
        )
        checks["lexical"] = "failed"
        return result

    roots: list[Node] = []
    stack: list[Node] = []
    prefixes: list[str] = []
    checks["lexical"] = "passed"
    for count, token in enumerate(tokens(text)):
        children = stack[-1].children if stack else roots
        if (token.kind != "eof" and count >= MAX_TOKENS) or (
            token.kind == "open" and len(stack) >= MAX_DEPTH
        ):
            add(
                "scan_limit",
                f"Preflight limit exceeded ({MAX_TOKENS} tokens, "
                f"{MAX_DEPTH} nested delimiters); split the source into smaller files.",
                token,
            )
            checks["lexical"] = "incomplete"
            break
        if token.kind == "error":
            add(token.value, LEXICAL_MESSAGES[token.value], token)
        elif token.kind == "prefix":
            prefixes.append(token.value)
            continue
        elif token.kind == "open":
            operator = None
            if (
                token.value == "("
                and children
                and not prefixes
                and children[-1].token.kind == "name"
                and children[-1].token.end == token.start
            ):
                previous = children.pop()
                operator = previous.token
                prefixes = list(previous.prefixes)
            node = Node(token, tuple(prefixes), operator)
            children.append(node)
            stack.append(node)
        elif token.kind == "close":
            expected_open = "(" if token.value == ")" else "["
            if not stack:
                message = f"Unexpected closing '{token.value}' with no matching opener."
                if token.value == "]":
                    message += " ']' is allowed only for indexing, never to close parentheses."
                add("unexpected_close", message, token)
            elif stack[-1].token.value != expected_open:
                opening = stack[-1].token
                expected = ")" if opening.value == "(" else "]"
                code = "super_right_bracket" if token.value == "]" else "mismatched_close"
                add(
                    code,
                    f"Expected '{expected}' before '{token.value}'. "
                    "Each '(' must close with ')'; ']' is reserved for indexing.",
                    token,
                    opening,
                )
                # Do not invent a recovery that changes which body owns later code.
                checks["lexical"] = "failed"
                break
            else:
                stack.pop()
        elif token.kind == "eof":
            for node in reversed(stack):
                opening = node.token
                code = "unclosed_paren" if opening.value == "(" else "unclosed_index"
                add(
                    code,
                    f"Opening '{opening.value}' is not closed before end of source.",
                    token,
                    opening,
                )
        else:
            children.append(Node(token, tuple(prefixes)))
        prefixes = []
    if errors:
        if checks["lexical"] == "passed":
            checks["lexical"] = "failed"
        return result
    checks["definition_policy"] = "passed"
    for code, message, token in definition_errors(roots):
        add(code, message, token)
    if errors:
        checks["definition_policy"] = "failed"
    checks["wire_policy"] = "passed"
    for code, message, token in wire_errors(roots, text):
        checks["wire_policy"] = "failed"
        add(code, message, token)
    result["ok"] = not errors
    return result


def require_preflight(source: bytes, *, source_name: str = "<code>") -> None:
    report = check_source(source, source_name=source_name)
    if not report["ok"]:
        first = report["errors"][0]
        position = f" at {source_name}:{first['line']}:{first['column']}" if "line" in first else ""
        raise RequestFailure(
            "skill_preflight_failed",
            f"SKILL preflight rejected the source{position}: {first['message']} "
            "Nothing was sent to Virtuoso; fix the source and check again.",
            report,
        )
