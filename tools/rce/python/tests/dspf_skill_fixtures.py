from __future__ import annotations

from pathlib import Path

from skill_test_support import read_skill_source

CAD_ROOT = Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return read_skill_source(CAD_ROOT / relative)



def _has_balanced_skill_parentheses(source: str) -> bool:
    balance = 0
    in_string = False
    in_comment = False
    escaped = False
    for character in source:
        if in_comment:
            if character == "\n":
                in_comment = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == ";":
            in_comment = True
        elif character == '"':
            in_string = True
        elif character == "(":
            balance += 1
        elif character == ")":
            balance -= 1
            if balance < 0:
                return False
    return balance == 0 and not in_string
