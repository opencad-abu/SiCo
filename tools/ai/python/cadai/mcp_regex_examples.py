"""Generate one bounded string from the regexes used by tool schemas."""

from __future__ import annotations

import re
from typing import Any


def regex_example(pattern: str) -> str:
    """Return one short likely match, verified by ``re.fullmatch``."""
    value = _Generator().value(pattern)
    if re.fullmatch(pattern, value):
        return value
    for candidate in ("example", "a" * 64, "0" * 64, "/tmp/example", "example-1"):
        if re.fullmatch(pattern, candidate):
            return candidate
    raise ValueError("unsupported regex pattern " + pattern)


class _Generator:
    def __init__(self) -> None:
        self.groups: dict[int, str] = {}

    def value(self, pattern: str) -> str:
        import sre_parse
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            parsed = sre_parse.parse(pattern)
        return self.part(parsed)

    def part(self, parsed: Any) -> str:
        pieces: list[str] = []
        for operation, argument in parsed:
            op = str(operation)
            if op == "LITERAL":
                pieces.append(chr(argument))
            elif op == "NOT_LITERAL":
                pieces.append("b" if argument == ord("a") else "a")
            elif op == "ANY":
                pieces.append("a")
            elif op == "IN":
                pieces.append(self.character_class(argument))
            elif op in {"MAX_REPEAT", "MIN_REPEAT"}:
                minimum, _maximum, child = argument
                pieces.append(self.part(child) * minimum)
            elif op == "SUBPATTERN":
                group, _add, _delete, child = argument
                value = self.part(child)
                if group:
                    self.groups[group] = value
                pieces.append(value)
            elif op == "BRANCH":
                options = [self.part(branch) for branch in argument[1]]
                pieces.append(min(options, key=len) if options else "")
            elif op in {"AT", "ASSERT", "ASSERT_NOT"}:
                pieces.append("")
            elif op == "GROUPREF":
                pieces.append(self.groups.get(argument, "a"))
            elif op == "CATEGORY":
                pieces.append(self.category(argument))
            else:
                raise ValueError("unsupported regex operation " + op)
        return "".join(pieces)

    @staticmethod
    def category(category: Any) -> str:
        return {
            "CATEGORY_DIGIT": "0",
            "CATEGORY_WORD": "a",
            "CATEGORY_SPACE": " ",
        }.get(str(category), "a")

    def character_class(self, items: Any) -> str:
        negate = any(str(operation) == "NEGATE" for operation, _ in items)
        allowed: list[str] = []
        for operation, argument in items:
            op = str(operation)
            if op == "LITERAL":
                allowed.append(chr(argument))
            elif op == "RANGE":
                allowed.append(chr(argument[0]))
            elif op == "CATEGORY":
                allowed.append(self.category(argument))
        if not negate:
            return allowed[0] if allowed else "a"
        for candidate in "abcXYZ019_-/.":
            if candidate not in allowed:
                return candidate
        return "q"


__all__ = ["regex_example"]
