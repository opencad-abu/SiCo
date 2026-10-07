"""Small SKILL lexer for preflight checks, independent of a Cadence runtime."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int
    column: int


def tokens(source: str) -> Iterator[Token]:
    """Yield significant tokens, lexical errors and EOF with Unicode positions.

    Comments do not nest. A physical newline immediately following a backslash
    continues a semicolon comment, including after multiple backslashes.
    Escaped symbol characters must not become delimiters or quote prefixes.
    """
    index, line, column = 0, 1, 1
    length = len(source)

    def advance(end: int) -> None:
        nonlocal index, line, column
        fragment = source[index:end]
        breaks = fragment.count("\n") + fragment.count("\r") - fragment.count("\r\n")
        if breaks:
            line += breaks
            column = len(fragment) - max(fragment.rfind("\n"), fragment.rfind("\r"))
        else:
            column += len(fragment)
        index = end

    def newline_end(at: int) -> int:
        return at + (2 if source.startswith("\r\n", at) else 1)

    while index < length:
        start, start_line, start_column = index, line, column
        char = source[index]
        end = index + 1
        kind, value = "other", char
        if char.isspace():
            while end < length and source[end].isspace():
                end += 1
            advance(end)
            continue
        if char == ";":
            while end < length:
                if source[end] in "\r\n":
                    if source[end - 1] != "\\":
                        break
                    end = newline_end(end)
                else:
                    end += 1
            advance(end)
            continue
        if source.startswith("/*", index):
            close = source.find("*/", index + 2)
            if close < 0:
                kind, value, end = "error", "unclosed_block_comment", length
            else:
                advance(close + 2)
                continue
        elif source.startswith("*/", index):
            kind, value, end = "error", "unexpected_comment_close", index + 2
        elif char == '"':
            kind, value = "error", "unclosed_string"
            while end < length:
                if source[end] == "\\":
                    end += 1
                    if end < length:
                        end = newline_end(end) if source[end] in "\r\n" else end + 1
                elif source[end] == '"':
                    kind, value, end = "string", "", end + 1
                    break
                else:
                    end += 1
        elif char in "([":
            kind = "open"
        elif char in ")]":
            kind = "close"
        elif char in "'`,":
            kind = "prefix"
            if source.startswith(",@", index):
                value, end = ",@", index + 2
        elif char.isalpha() or char in "_?@$\\":
            kind = "name"
            pieces: list[str] = []
            end = index
            while end < length:
                current = source[end]
                if current.isalnum() or current in "_?@$":
                    pieces.append(current)
                    end += 1
                elif current == "\\":
                    end += 1
                    if end == length:
                        kind, value = "error", "dangling_escape"
                        break
                    if source[end] in "\r\n":
                        end = newline_end(end)
                    elif source[end] in "01234567":
                        limit = end + 3
                        first = end
                        while end < min(length, limit) and source[end] in "01234567":
                            end += 1
                        pieces.append(chr(int(source[first:end], 8)))
                    else:
                        pieces.append(source[end])
                        end += 1
                else:
                    break
            if kind == "name":
                value = "".join(pieces)
                if not value:  # a standalone continued line
                    advance(end)
                    continue
        yield Token(kind, value, start, end, start_line, start_column)
        advance(end)
    yield Token("eof", "", index, index, line, column)
