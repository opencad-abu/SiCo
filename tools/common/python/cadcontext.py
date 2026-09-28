"""Locate protected workers and serialize their invocation arguments."""

import os
from pathlib import Path

from sicoenv import value


def skill_value(value):
    if value is None or value is False:
        return "nil"
    if value is True:
        return "t"
    if isinstance(value, (list, tuple)):
        return "list(" + " ".join(skill_value(item) for item in value) + ")"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("control character in context argument")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def worker_call(name, *args):
    root = Path(value(os.environ, "SICO_RUNTIME_TOOLS", ("CAD_RUNTIME_TOOLS",), Path(__file__).resolve().parents[2]))
    context = root / "context" / "cadWorkers.cxt"
    if not context.is_file():
        raise RuntimeError(f"Protected CAD worker context is unavailable: {context}")
    if not name.isidentifier():
        raise ValueError("invalid context procedure name")
    return (
        f"unless(isCallable('{name}) loadContext({skill_value(context)} t))\n"
        + name + "(" + " ".join(skill_value(value) for value in args) + ")\n"
    )
