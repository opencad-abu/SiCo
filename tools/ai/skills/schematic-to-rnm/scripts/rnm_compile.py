"""Optional scratch-directory xrun syntax check and diagnostic classification."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from rnm_findings import Finding

# Diagnostics that describe the surrounding environment rather than the module:
# a stale cds.lib path, and the expected "no top level" note when a single leaf
# module is compiled on its own.
ENVIRONMENT_NOISE_RE = re.compile(r"\*[EWF],(?:DLCPTH|NOTOPL)\b")

# Once `include "disciplines.vams" fails, wreal is an unknown type and every
# port declaration that uses it is reported again as a duplicate or unknown
# identifier.  These codes are symptoms of that one failure, not defects.
CASCADED_AMS_ERROR_RE = re.compile(r"\*[EWF],(?:DUPIDN|NOIPRT|VLGERR|SVNOTY)\b")


def compile_check(path: Path, extra_args: Iterable[str]) -> list[Finding]:
    executable = shutil.which("xrun")
    if executable is None:
        return [
            Finding(
                "warning",
                "compile",
                path.name,
                "xrun is not on PATH; syntax was not checked",
            )
        ]

    with tempfile.TemporaryDirectory(prefix="rnm_compile_") as scratch:
        command = [
            executable,
            "-compile",
            "-sv",
            "-nolog",
            "-xmlibdirpath",
            scratch,
            *extra_args,
            str(path),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return [
                Finding("warning", "compile", path.name, "xrun -compile timed out")
            ]
        except OSError as exc:
            return [
                Finding("warning", "compile", path.name, f"cannot run xrun: {exc}")
            ]

    output = f"{completed.stdout}\n{completed.stderr}"

    # A Verilog-AMS source needs AMS mode; under plain -sv the disciplines
    # include fails and every later wreal declaration is then misparsed.  Report
    # the cause once instead of the cascade it produces.
    ams_include_failed = bool(
        re.search(r"\*E,COFILX.*disciplines\.vams", output, re.IGNORECASE)
    )

    findings: list[Finding] = []
    seen: set[str] = set()

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or ENVIRONMENT_NOISE_RE.search(stripped):
            continue
        if ams_include_failed and CASCADED_AMS_ERROR_RE.search(stripped):
            continue
        if ams_include_failed and re.search(
            r"\*[EWF],COFILX.*disciplines\.vams", stripped, re.IGNORECASE
        ):
            continue
        if stripped in seen:
            continue

        if re.search(r"\*[EF],", stripped):
            seen.add(stripped)
            findings.append(Finding("error", "compile", path.name, stripped))
        elif re.search(r"\*W,", stripped):
            seen.add(stripped)
            findings.append(Finding("warning", "compile", path.name, stripped))

    if ams_include_failed:
        findings.append(
            Finding(
                "warning",
                "compile",
                path.name,
                "source includes disciplines.vams (Verilog-AMS); syntax was not "
                "checked in pure SystemVerilog mode. A generated RNM should "
                "instead import cds_rnm_pkg and drop the include, or be compiled "
                "with AMS enabled.",
            )
        )
        return findings

    if completed.returncode != 0 and not findings:
        findings.append(
            Finding(
                "error",
                "compile",
                path.name,
                f"xrun -compile exited {completed.returncode}",
            )
        )
    if completed.returncode == 0 and not findings:
        findings.append(
            Finding("info", "compile", path.name, "xrun -compile succeeded")
        )
    return findings
