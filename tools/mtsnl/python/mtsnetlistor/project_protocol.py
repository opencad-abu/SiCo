"""SKILL protocol for one project-owned OCEAN process."""

from __future__ import annotations

from pathlib import Path
import re


def skill_string(value: str | Path) -> str:
    text = str(value)
    if any(ord(char) < 32 for char in text):
        raise ValueError("control character in project worker argument")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def task_script(text: str) -> str:
    """Remove only the generated worker's final exit, retaining setup code."""

    matches = list(re.finditer(r"(?m)^[ \t]*exit\(\)[ \t]*$", text))
    if len(matches) != 1:
        raise ValueError("project worker expects exactly one generated exit()")
    match = matches[0]
    result = text[: match.start()] + "  mtsProjectTaskComplete=t" + text[match.end() :]
    return result.replace(
        "baseline=mtsDefaultsSnapshot(tool session)",
        "baseline=mtsProjectBaseline(simulator())",
    )


def bootstrap(root: Path, defaults_helpers: str) -> str:
    from cadcontext import worker_call
    return worker_call("mtsRuntimeProject", root) + "exit()\n"
