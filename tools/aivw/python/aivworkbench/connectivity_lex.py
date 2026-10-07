"""Lexical normalization for the bounded SI Verilog subset."""

from __future__ import annotations

import re

from .connectivity_model import ConnectivityParseError


def unescape_identifier(value: str) -> str:
    """Normalize an SV escaped identifier (the trailing space is syntax)."""
    value = value.strip()
    if value.startswith("\\"):
        return value[1:].rstrip()
    return value


def split_tokens(value: str) -> list[str]:
    """Split comma-separated Verilog expressions, respecting brackets/parens."""
    parts: list[str] = []
    start = 0
    depth = 0
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                raise ConnectivityParseError("unbalanced delimiter in connection list")
        elif char == "," and depth == 0:
            token = value[start:index].strip()
            if not token:
                raise ConnectivityParseError("empty connection expression")
            parts.append(token)
            start = index + 1
    if depth != 0:
        raise ConnectivityParseError("unbalanced delimiter in connection list")
    token = value[start:].strip()
    if token:
        parts.append(token)
    elif value.strip():
        raise ConnectivityParseError("empty final connection expression")
    return parts


def canonical_expr(value: str) -> str:
    value = re.sub(r"\s+", " ", value.strip())
    return value


def strip_comments(text: str) -> str:
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
