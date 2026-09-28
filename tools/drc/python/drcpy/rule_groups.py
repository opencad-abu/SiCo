"""Static rule GROUP definitions and ordered check membership."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from .rule_syntax import RULE_NAME, command_words, logical_lines, strip_comments


_GROUP_STATEMENT = re.compile(r"^(?:tvf::)?group(?:\s+|$)", re.IGNORECASE)


@dataclass(frozen=True)
class RuleGroupInfo:
    """Rule-group discovery result suitable for UI/CLI serialization."""

    groups: tuple[str, ...]
    counts: dict[str, int]
    source: str
    error: str | None = None
    cached: bool = False
    members: dict[str, tuple[str, ...]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": list(self.groups),
            "counts": dict(self.counts),
            "members": {
                name: list((self.members or {}).get(name, ()))
                for name in self.groups
            },
            "source": self.source,
            "error": self.error,
            "cached": self.cached,
        }


def parse_rule_groups(text: str) -> list[str]:
    """Return statically declared GROUP names in source order.

    This is intentionally a best-effort source parser.  It recognizes plain
    SVRF and compile-time TVF GROUP statements, but it does not evaluate Tcl.
    Names containing unresolved Tcl substitutions are therefore omitted.
    """
    return [name for name, _ in group_definitions(text)]


def group_definitions(text: str) -> list[tuple[str, tuple[str, ...]]]:
    definitions: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for statement in logical_lines(text):
        stripped = statement.lstrip()
        match = _GROUP_STATEMENT.match(stripped)
        if match is None:
            continue
        words = command_words(stripped[match.end() :])
        if len(words) < 2:
            continue
        name = words[0]
        if not RULE_NAME.fullmatch(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        definitions.append((name, tuple(words[1:])))
    return definitions


def group_check_members(
    text: str, definitions: list[tuple[str, tuple[str, ...]]]
) -> dict[str, tuple[str, ...]]:
    """Resolve the ordered rule-check members matched by each GROUP."""
    check_names = _rule_check_names(text)
    group_names = {name.casefold(): name for name, _ in definitions}
    operands_by_group = {
        name.casefold(): operands for name, operands in definitions
    }

    regexp_groups = bool(
        re.search(r"^\s*#USING\s+REGEXP\s+GROUP\b", text, re.I | re.M)
    )
    resolved: dict[str, set[str]] = {}

    def resolve_members(group: str, active: set[str]) -> set[str]:
        key = group.casefold()
        if key in resolved:
            return resolved[key]
        if key in active:
            return set()
        matched: set[str] = set()
        active = active | {key}
        for operand in operands_by_group.get(key, ()):
            operand_key = operand.casefold()
            if operand_key in operands_by_group:
                matched.update(resolve_members(operand_key, active))
            elif regexp_groups and operand.startswith("/"):
                try:
                    expression = re.compile(operand[1:])
                except re.error:
                    continue
                matched.update(name for name in check_names if expression.search(name))
            else:
                matched.update(
                    name
                    for name in check_names
                    if _svrf_wildcard_match(operand, name, case_sensitive=False)
                )
        resolved[key] = matched
        return matched

    return {
        name: tuple(check for check in check_names if check in resolve_members(key, set()))
        for key, name in group_names.items()
    }


def _rule_check_names(text: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(r"^\s*([A-Za-z0-9_][A-Za-z0-9_.:+@-]*)\s*\{")
    for line in strip_comments(text).splitlines():
        match = pattern.match(line)
        if match is None:
            continue
        name = match.group(1)
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return tuple(names)


def _svrf_wildcard_match(
    pattern: str, name: str, *, case_sensitive: bool
) -> bool:
    expression = re.escape(pattern).replace(r"\?", ".*")
    if not case_sensitive:
        expression = f"(?i:{expression})"
    return re.fullmatch(expression, name) is not None
