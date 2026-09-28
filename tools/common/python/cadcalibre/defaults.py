"""Validate static SVRF defaults used by the Calibre control decks."""

from __future__ import annotations

import re

from caddefaults import Defaults, resolve_defaults
from caddefaults.syntax import rows, sections

# These namespaces belong to the GUI, request, or data exchange protocol.
_RESERVED = (
    "LAYOUT", "SOURCE", "MASK", "INCLUDE", "VIRTUAL CONNECT", "LVS REPORT",
    "LVS RECOGNIZE GATES", "HCELL", "DRC SELECT", "DRC UNSELECT",
    "DRC RESULTS", "DRC SUMMARY", "ERC RESULTS", "PEX NETLIST",
    "PEX PIN ORDER", "PEX EXTRACT", "PEX REDUCE",
)


def calibre_defaults(
    name: str, defaults: Defaults | None, log_dir,
) -> dict[str, list[str]]:
    source = resolve_defaults((name,), defaults, log_dir).source(name)
    groups = (
        sections(source, {"before_erc_results", "after_erc_results"}, comments=("//", "#"))
        if name == "calibre-lvs.svrf"
        else {"default": list(rows(source, ("//",)))}
    )
    seen = set()
    for records in groups.values():
        for number, line in records:
            normalized = " ".join(line.upper().split())
            if not re.match(r"^[A-Za-z][A-Za-z0-9 ]*\s+", line) or any(
                char in line for char in (";", "{", "}", "\\", "#", "$")
            ):
                raise source.error(number, "Expected a plain SVRF setting, no include or script")
            if any(normalized == prefix or normalized.startswith(prefix + " ")
                   for prefix in _RESERVED):
                raise source.error(number, f"GUI or flow-owned SVRF setting: {line}")
            if normalized in seen:
                raise source.error(number, f"Repeated SVRF setting: {line}")
            seen.add(normalized)
    return {key: [line for _, line in records] for key, records in groups.items()}
