from __future__ import annotations

from pathlib import Path

CAD_ROOT = Path(__file__).resolve().parents[3]
SKILL_FILES = (
    "lef/skill++/LEF.ils",
    "lef/skill++/LEFCFG.ils",
    "lef/skill++/LEFOPT.ils",
    "lef/skill++/LEFCB.ils",
    "lef/skill++/LEFRUN.ils",
    "lef/skill++/LEFLOAD.ils",
    "lef/skill++/LEFPROFILE.ils",
    "lef/skill++/LEFGUI.ils",
    "lef/skill/UI_lefSummary.il",
)


def _balanced(source: str) -> bool:
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
