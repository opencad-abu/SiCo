"""Canonical GROUP/CHECK records and their atomic GUI transfer protocol."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from cadgui.protocol import read_transfer, write_transfer
from .rule_syntax import RULE_NAME


@dataclass(frozen=True)
class RuleSelection:
    """Canonical Rule Select output records."""

    groups: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.groups or self.checks)


def _validated_record(record: str, name: str, *, line_number: int) -> str:
    candidate = name.strip()
    if record not in {"GROUP", "CHECK"}:
        raise ValueError(
            f"Unsupported Rule Select record {record!r} on line {line_number}"
        )
    if not RULE_NAME.fullmatch(candidate):
        raise ValueError(f"Invalid Rule Select name {name!r} on line {line_number}")
    return candidate


def read_initial_selection(path: str | Path | None) -> RuleSelection:
    """Read the small GROUP/CHECK TSV passed by the Virtuoso launcher."""
    if path is None:
        return RuleSelection()
    groups: list[str] = []
    checks: list[str] = []
    seen_groups: set[str] = set()
    seen_checks: set[str] = set()
    document = read_transfer(path)
    for line_number, (raw_record, raw_name) in enumerate(document.records, 1):
        record = raw_record.strip().upper()
        name = _validated_record(record, raw_name, line_number=line_number)
        key = name.casefold()
        if record == "GROUP" and key not in seen_groups:
            groups.append(name)
            seen_groups.add(key)
        elif record == "CHECK" and key not in seen_checks:
            checks.append(name)
            seen_checks.add(key)
    return RuleSelection(tuple(groups), tuple(checks))


def write_selection(path: str | Path, selection: RuleSelection) -> None:
    """Atomically publish an applied selection for the SKILL post callback."""
    records = [("GROUP", name) for name in selection.groups]
    records.extend(("CHECK", name) for name in selection.checks)
    write_transfer(
        path,
        records,
        status="applied",
        refuse_existing=True,
    )
