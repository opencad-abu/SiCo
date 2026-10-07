"""Validated argv construction for physical PVT launchers."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Sequence

from .artifact_paths import path_has_symlink_component
from .pvt_launcher import PhysicalPVTLauncher
from .pvt_plan_models import PVTLaunchContext
from .pvt_paths import approved_executable

_CONTROL_CHARS = frozenset("\x00\r\n;|&`$\\")

def validate_command(
    raw_command: object,
    launcher: PhysicalPVTLauncher,
    context: PVTLaunchContext,
) -> str | None:
    if not isinstance(raw_command, Sequence) or isinstance(raw_command, (str, bytes)):
        return "command builder must return an argv array"
    command = tuple(raw_command)
    if not command or any(not isinstance(item, str) or not item for item in command):
        return "command argv must contain non-empty text arguments"
    if any(any(character in _CONTROL_CHARS for character in item) for item in command):
        return "command argv contains shell/control characters"
    if any(re.match(r"^[A-Za-z]:", item) for item in command):
        return "command argv contains a Windows drive path"
    expected_tool = context.tools.get(launcher.command_tool)
    if not isinstance(expected_tool, str) or not approved_executable(Path(expected_tool)):
        return f"qualified command tool is unavailable: {launcher.command_tool}"
    if command[0] != expected_tool:
        return "command argv[0] does not match the approved launcher path"
    if str(context.deck_path) not in command:
        return "command does not bind the approved point deck path"
    if str(context.model_path) not in command:
        return "command does not bind the approved model file path"
    if context.model_section not in command:
        return "command does not bind the approved model section"
    if str(context.output_path) not in command:
        return "command does not bind the approved raw output path"
    try:
        approved_roots = (
            context.model_root.resolve(strict=False),
            context.deck_root.resolve(strict=False),
            context.payload_root.resolve(strict=False),
        )
    except (OSError, RuntimeError) as exc:
        return f"approved command roots could not be resolved: {exc}"
    for argument in command[1:]:
        if not argument.startswith("/"):
            continue
        candidate = Path(argument)
        if candidate == expected_tool:
            continue
        try:
            physical = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            return f"command path could not be resolved: {argument}"
        lexical_root = next(
            (
                root
                for root in approved_roots
                if lexically_under(root, candidate)
            ),
            None,
        )
        if lexical_root is not None and path_has_symlink_component(
            lexical_root, candidate
        ):
            return f"command contains a symlink path component: {argument}"
        if not any(physical.is_relative_to(root) for root in approved_roots):
            return f"command contains an absolute path outside approved roots: {argument}"
    if any(
        item == ".." or item.startswith("../") or "/../" in item
        for item in command
    ):
        return "command argv contains path traversal"
    return None


def lexically_under(root: Path, candidate: Path) -> bool:
    """Return containment without resolving symlinks."""

    try:
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


__all__ = ["validate_command"]
