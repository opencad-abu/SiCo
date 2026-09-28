"""Logical statements with separate source and parsing representations."""

from __future__ import annotations

from dataclasses import dataclass


class ScopeError(ValueError):
    """Raw netlist cannot be safely scoped."""


@dataclass(frozen=True)
class Statement:
    text: str
    start_line: int
    end_line: int

    @property
    def stripped(self) -> str:
        return self.text.strip()

    def is_comment(self, dialect: str) -> bool:
        """Recognize generated ``**`` and handwritten ``*`` HSPICE comments."""

        if dialect == "hspiceD":
            return self.stripped.startswith(("*", "$"))
        return self.stripped.startswith("//")

    def parsed(self, dialect: str) -> str:
        """Fold Spectre continuations without changing escaped identifiers.

        Rendering always uses ``text``. Only a physical line's continuation
        marker is removed here; backslashes in bus pins and quoted paths
        remain data. HSPICE keeps its existing parsing representation.
        """

        if dialect != "spectre" or "\n" not in self.text:
            return self.stripped
        lines = self.text.split("\n")
        for index, line in enumerate(lines[:-1]):
            if _spectre_continues(line):
                lines[index] = line.rstrip()[:-1]
        return " ".join(lines).strip()


def _spectre_continues(line: str) -> bool:
    """Whether a Spectre physical line has an unquoted, unescaped final ``\\``."""

    text = line.rstrip()
    if not text.endswith("\\"):
        return False
    quoted = False
    escaped = False
    for char in text[:-1]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
    return not quoted and not escaped


def logical_statements(text: str, dialect: str) -> tuple[Statement, ...]:
    physical = text.splitlines()
    statements: list[Statement] = []
    current: list[str] = []
    start = 0
    previous_continues = False
    for line_number, line in enumerate(physical, start=1):
        if dialect == "hspiceD" and line.startswith("+") and not current:
            raise ScopeError(f"orphan HSPICE continuation at line {line_number}")
        continuation = bool(current) and (
            previous_continues or (dialect == "hspiceD" and line.startswith("+"))
        )
        if continuation:
            # Preserve physical text and indentation for the renderer.
            current.append(line)
            previous_continues = dialect == "spectre" and _spectre_continues(line)
            continue
        if current:
            statements.append(Statement("\n".join(current), start, line_number - 1))
        current = [line]
        start = line_number
        previous_continues = dialect == "spectre" and _spectre_continues(line)
    if current:
        statements.append(Statement("\n".join(current), start, len(physical)))
    return tuple(statements)
