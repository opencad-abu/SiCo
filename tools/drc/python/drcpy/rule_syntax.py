"""Lexical rules shared by SVRF and statically inspectable TVF commands."""

from __future__ import annotations

import re


RULE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:+@-]*\Z")


def logical_lines(text: str) -> list[str]:
    lines = strip_comments(text).splitlines()
    logical: list[str] = []
    pending = ""
    for line in lines:
        trimmed = line.rstrip()
        if trimmed.endswith("\\"):
            pending += trimmed[:-1] + " "
            continue
        logical.append(pending + line)
        pending = ""
    if pending:
        logical.append(pending)
    return logical


def strip_comments(text: str) -> str:
    """Remove C/SVRF comments while retaining newlines for statement parsing."""
    output: list[str] = []
    index = 0
    in_block = False
    in_quote = False
    escaped = False
    line_has_code = False
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_block:
            if char == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                in_block = False
                continue
            output.append("\n" if char == "\n" else " ")
            if char == "\n":
                line_has_code = False
            index += 1
            continue
        if not in_quote and char == "/" and following == "*":
            output.extend((" ", " "))
            index += 2
            in_block = True
            continue
        if not in_quote and char == "/" and following == "/":
            while index < len(text) and text[index] != "\n":
                output.append(" ")
                index += 1
            continue
        if not in_quote and char == "#" and not line_has_code:
            while index < len(text) and text[index] != "\n":
                output.append(" ")
                index += 1
            continue

        output.append(char)
        if char == "\n":
            line_has_code = False
            in_quote = False
            escaped = False
        else:
            if char == '"' and not escaped:
                in_quote = not in_quote
            if not char.isspace():
                line_has_code = True
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        index += 1
    return "".join(output)


def command_words(command: str) -> list[str]:
    """Tokenize enough Tcl/SVRF syntax to inspect a GROUP declaration."""
    words: list[str] = []
    index = 0
    while index < len(command):
        while index < len(command) and command[index].isspace():
            index += 1
        if index >= len(command) or command[index] == ";":
            break

        opening = command[index]
        if opening in {'"', "{"}:
            closing = '"' if opening == '"' else "}"
            index += 1
            start = index
            escaped = False
            depth = 1
            while index < len(command):
                char = command[index]
                if opening == "{" and not escaped:
                    if char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            break
                elif opening == '"' and char == closing and not escaped:
                    break
                escaped = char == "\\" and not escaped
                if char != "\\":
                    escaped = False
                index += 1
            if index >= len(command):
                return words
            words.append(command[start:index])
            index += 1
            continue

        start = index
        while (
            index < len(command)
            and not command[index].isspace()
            and command[index] != ";"
        ):
            index += 1
        words.append(command[start:index])
    return words
