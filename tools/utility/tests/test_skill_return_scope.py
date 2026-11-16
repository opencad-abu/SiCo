"""Every SKILL return must resolve to a prog inside the same function."""

from pathlib import Path

from utility.skill_loader_contract import manifest
from utility.skill_syntax import parse

ROOT = Path(__file__).resolve().parents[3]
FUNCTION_FORMS = {"procedure", "lambda", "defun", "defmacro", "defmethod", "defgeneric"}


def sources():
    group_manifest = manifest(ROOT)
    paths = {path for group in group_manifest.values() for path in group["sources"]}
    return sorted(path for path in paths if path.endswith(".il"))


def enclosing_function(frame):
    node = frame.parent
    while node is not None:
        if node.operator in FUNCTION_FORMS:
            return node
        node = node.parent
    return None


def has_enclosing_prog(frame):
    node = frame.parent
    while node is not None:
        operator = node.operator
        if operator == "prog":
            return True
        if operator in FUNCTION_FORMS:
            return False
        node = node.parent
    return False


def test_skill_returns_stay_inside_prog():
    offenders = []
    for relative in sources():
        _, frames = parse((ROOT / relative).read_text(encoding="utf-8"))
        for frame in frames:
            if frame.operator != "return" or frame.quoted or has_enclosing_prog(frame):
                continue
            owner = enclosing_function(frame)
            name = owner.children[0].algebraic_name if owner is not None else "?"
            offenders.append(f"{relative}:{frame.open_token.line} {name}")
    assert not offenders, "return outside prog: " + ", ".join(offenders)
