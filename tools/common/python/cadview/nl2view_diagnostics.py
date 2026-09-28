"""Classify Cadence import diagnostics and record their wrapper evidence."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional, Sequence


def new_log_diagnostics(log_file: Optional[Path], before: Optional[bytes]) -> str:
    if log_file is None or not log_file.is_file():
        return ""
    after = log_file.read_bytes()
    if before is not None and after == before:
        return ""
    if before and after.startswith(before):
        after = after[len(before) :]
    return after.decode("utf-8", errors="replace")


# ``cdsTextTo5x`` initializes the Generic Design Management (GDM) layer even
# when it only imports an analog text view.  A target library may advertise an
# optional vendor DM plug-in (currently seen as ``DMTYPE aivivc``), while the
# corresponding ``aivivcgdmconfig`` executable/shared library is absent from
# the Cadence installation.  In that case the importer still creates a valid
# OA view and exits successfully; treating these startup messages as parser
# errors makes publication fail after the target has already been mutated.
_CADENCE_ERROR_PREFIX_RE = re.compile(r"(?i)^\s*\**\s*(?:error|fatal)\b")
_GDM_ERROR_LINE_RE = re.compile(
    r"(?i)^\s*\**\s*Error\s+"
    r"\((?P<kind>gdmForkExec|gdmLoadSharedLib|gdmiLoadDMLibrary|"
    r"gdmImportDMSystem)\):(?P<body>.*)$"
)
_OPTIONAL_GDM_AIVIVC_MARKER_RE = re.compile(
    r"(?i)(?:aivivcgdmconfig|libgdmaivivc_sh\.so|"
    r"DM system ['\"]?aivivc['\"]?)"
)
_GDM_CONTACT_OWNER_RE = re.compile(r"(?i)contact the owner of the library")


def has_fatal_cadence_diagnostic(diagnostics: str) -> bool:
    """Return whether Cadence output contains an actionable error/fatal line.

    The missing ``aivivc`` GDM integration is an optional environment issue,
    not an import failure, when ``cdsTextTo5x`` has returned zero.  Skip only
    the narrowly-scoped diagnostic lines from that startup block before
    looking for ordinary ``ERROR``/``FATAL`` messages.  Any other GDM error,
    including a real importer failure, remains fatal.
    """

    # The generic "Contact the owner ..." line has no plugin name itself. It
    # is ignored only when it immediately follows a known aivivc shared-library
    # diagnostic; an unrelated GDM contact error remains actionable.
    optional_loader_gap: int | None = None
    for line in diagnostics.splitlines():
        if not _CADENCE_ERROR_PREFIX_RE.match(line):
            # Cadence inserts blank and explanatory continuation lines between
            # the GDM messages. Keep a small bounded gap for the generic
            # "Contact the owner" continuation, but do not let that context
            # leak across an unrelated part of the diagnostic stream.
            if optional_loader_gap is not None:
                optional_loader_gap += 1
            continue
        match = _GDM_ERROR_LINE_RE.match(line)
        if match is None:
            return True
        kind = match.group("kind")
        body = match.group("body")
        if _OPTIONAL_GDM_AIVIVC_MARKER_RE.search(body):
            optional_loader_gap = 0 if kind == "gdmLoadSharedLib" else None
            continue
        if (
            optional_loader_gap is not None
            and optional_loader_gap <= 2
            and kind == "gdmLoadSharedLib"
            and _GDM_CONTACT_OWNER_RE.search(body)
        ):
            optional_loader_gap = None
            continue
        optional_loader_gap = None
        return True
    return False


def append_wrapper_diagnostics(
    log_file: Optional[Path],
    command: Sequence[str],
    *,
    returncode: Optional[int],
    outcome: str,
    stdout: str = "",
    stderr: str = "",
    detail: str = "",
) -> None:
    """Leave deterministic evidence even when Cadence does not write ``-LOG``.

    IC23.10 can complete ``cdsTextTo5x`` without creating the requested log
    file.  Append a wrapper-owned section after Cadence has exited so GUI
    publication always has a concrete evidence path.  JSON-rendering argv
    keeps whitespace and control characters unambiguous in the report.
    """

    if log_file is None:
        return
    lines = [
        "",
        "--- nl2view wrapper diagnostics ---",
        "command_argv: " + json.dumps(list(command), ensure_ascii=True),
        f"returncode: {returncode if returncode is not None else 'not-started'}",
        f"outcome: {outcome}",
    ]
    if detail:
        lines.extend(("detail:", detail.rstrip()))
    if stdout:
        lines.extend(("stdout:", stdout.rstrip()))
    if stderr:
        lines.extend(("stderr:", stderr.rstrip()))
    payload = "\n".join(lines).rstrip() + "\n"
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(payload)
    except OSError as exc:
        # Evidence failure must not hide the actual Cadence/import result.
        print(f"nl2view: cannot write diagnostic log {log_file}: {exc}", file=sys.stderr)
