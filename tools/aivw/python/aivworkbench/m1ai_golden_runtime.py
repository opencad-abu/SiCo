"""Process and artifact helpers for the M1 nominal golden gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence

from .errors import ProfileError
from .process import ProcessResult
from .profiles import Profile
from .workspace import sha256_file


ProcessRunner = Callable[..., ProcessResult]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tool_runtime_version(version_output: str) -> str:
    match = re.search(r"\d+(?:\.[A-Za-z0-9]+)+", version_output)
    if not match:
        raise ValueError("cannot derive the approved Spectre runtime version")
    return match.group(0)


def cadence_environment(
    environment: Mapping[str, str],
    *,
    project: Path,
    work: Path,
) -> dict[str, str]:
    child = dict(environment)
    child.update(
        {
            "PROJECT": str(project),
            "CDS_SITE": str(project / "setup" / "site"),
            "CDS_DB_TYPE": "oa",
            "CDS_AUTO_64BIT": "ALL",
            "PWD": str(work),
        }
    )
    return child


def golden_workers(root: Path) -> dict[str, Path]:
    return {
        "rebind": root / "skill" / "m1ai_rebind_worker.il",
        "schcheck": root / "skill" / "m1ai_schcheck_worker.il",
        "nominal": root / "skill" / "m1ai_nominal_worker.il",
        "pvt": root / "skill" / "m1ai_pvt_worker.il",
    }


class PhaseFailure(Exception):
    """A classified M1 execution failure with optional process evidence."""

    def __init__(
        self,
        status: str,
        detail: str,
        result: ProcessResult | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.result = result


def project_path(profile: Profile, key: str) -> Path:
    value = profile.projects.get("adc", {}).get(key)
    if not value:
        raise ProfileError(f"profile ADC project has no {key!r}")
    path = Path(str(value)).expanduser()
    if not path.is_absolute() or "smic28" in str(path).lower():
        raise ProfileError(f"unsafe profile project path: {path}")
    return path


def run_phase(
    runner: ProcessRunner,
    command: tuple[str, ...],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_file: Path,
    timeout: float,
    failure_status: str,
) -> ProcessResult:
    result = runner(
        command,
        cwd=cwd,
        environment=environment,
        log_file=log_file,
        timeout=timeout,
    )
    text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.is_file() else ""
    lowered = text.lower()
    license_markers = (
        "license checkout failed",
        "no valid license",
        "required license could not be checked out",
        "waiting for available license for spectre",
        "fatal (spectre-209)",
    )
    if (result.timed_out or result.returncode != 0) and any(
        marker in lowered for marker in license_markers
    ):
        raise PhaseFailure(
            "BLOCKED_LICENSE",
            f"{command[0]} could not obtain a license",
            result,
        )
    if result.timed_out:
        raise PhaseFailure("TIMEOUT", f"{command[0]} timed out", result)
    if result.returncode != 0:
        raise PhaseFailure(
            failure_status,
            f"{command[0]} exited with {result.returncode}",
            result,
        )
    return result


def run_spectre_preflight(
    runner: ProcessRunner,
    spectre: str,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log_file: Path,
    timeout: float,
    expected_version: str,
) -> ProcessResult:
    cwd.mkdir()
    netlist = cwd / "input.scs"
    netlist.write_text(
        "simulator lang=spectre\n"
        "V0 (n 0) vsource dc=1\n"
        "R0 (n 0) resistor r=1k\n"
        "dcOp dc\n",
        encoding="utf-8",
    )
    result = run_phase(
        runner,
        (
            spectre,
            str(netlist),
            "+lqtimeout",
            "10",
            "-format",
            "psfxl",
            "-raw",
            str(cwd / "psf"),
        ),
        cwd=cwd,
        environment={**environment, "PWD": str(cwd)},
        log_file=log_file,
        timeout=timeout,
        failure_status="FAIL_SPECTRE_PREFLIGHT",
    )
    require_log(
        log_file,
        (f"Version {expected_version}", "spectre completes with 0 errors"),
        "FAIL_SPECTRE_PREFLIGHT",
    )
    return result


def require_log(path: Path, markers: Sequence[str], status: str) -> None:
    if not path.is_file():
        raise PhaseFailure(status, f"required phase log is missing: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise PhaseFailure(status, f"phase log has no required markers: {missing}")


def require_under(path: Path, root: Path, *, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise PhaseFailure(
            "FAIL_PATH_CONTRACT",
            f"{label} escaped the immutable run directory: {path}",
        ) from exc


def artifact_index(root: Path, excluded: set[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path not in excluded:
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return records


__all__ = [
    "PhaseFailure",
    "ProcessRunner",
    "artifact_index",
    "cadence_environment",
    "golden_workers",
    "project_path",
    "require_log",
    "require_under",
    "run_phase",
    "run_spectre_preflight",
    "tool_runtime_version",
    "utc_now",
]
