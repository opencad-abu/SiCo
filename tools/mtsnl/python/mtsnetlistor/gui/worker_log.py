"""Display Cadence diagnostics without echoed SKILL input or return values."""

from __future__ import annotations

import re


_PREFIX = re.compile(r"^\s*\\([A-Za-z#])")
_PREFIXES = re.compile(r"^\s*(?:(?:\\[A-Za-z#])\s*)+")
# Cadence replay records: input, accelerated input, prompt, accelerated return,
# typed return. Inspect the record type before removing its display prefix.
_ECHO_TYPES = frozenset("iaprt")


def _record_type(line: str) -> str:
    match = _PREFIX.match(line)
    return match.group(1) if match else ""


def clean_worker_log_line(line: str) -> str:
    return _PREFIXES.sub("", line).strip()


def safe_catalog_log(line: str) -> bool:
    """Keep catalog diagnostics, excluding code, protocol and secret material."""

    if _record_type(line) in _ECHO_TYPES:
        return False
    text = clean_worker_log_line(line)
    if not text or len(text) > 4096 or "\x00" in text:
        return False
    if text.startswith(("{", "[")) and (
        '"schema_version"' in text or '"libraries"' in text
    ):
        return False
    upper = text.upper()
    if "CDS_MPS_" in upper or "CADVIEW_CATALOG_OUTPUT" in upper:
        return False
    if re.search(
        r"(?i)(?:^|\b)(?:API[_ -]?KEY|ACCESS[_ -]?TOKEN|AUTHORIZATION|"
        r"PASSWORD|PASSWD|SECRET|LICENSE_FILE|LM_LICENSE_FILE)\b\s*[:=]",
        text,
    ):
        return False
    if re.match(r"(?i)^\s*(?:export\s+|setenv\s+)?[A-Z_][A-Z0-9_]*\s*=", text):
        return False
    return True


def useful_worker_log(line: str) -> bool:
    """Keep runtime progress and complete warning/error records for the GUI."""

    kind = _record_type(line)
    if kind in _ECHO_TYPES:
        return False
    text = clean_worker_log_line(line)
    if not text:
        return False
    # Warning/error continuation records often omit the severity keyword.
    if kind in {"w", "e"}:
        return True
    upper = text.upper()
    return any(
        marker in upper
        for marker in (
            "MTS_NETLISTOR_",
            "ERROR",
            "WARNING",
            "FATAL",
            "INFO",
            "CREATENETLIST",
            "MODELFILE",
            "NETLIST",
        )
    )
