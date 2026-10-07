"""Stage and validate scratch-only M1 comparator golden inputs/results."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import shutil
from typing import Mapping

from .errors import EnvironmentError
from .m0s_inputs import tree_identity


_PROJECT_DIR = re.compile(
    r'(<field Name="projectDir" Type="string">")([^"\n]*)("</field>)'
)
_SPECTRE_VERSION = re.compile(r"^Version\s+(?P<version>\S+)", re.MULTILINE)
_LICENSE_FAILURES = (
    "FATAL (SPECTRE-209)",
    "required license could not be checked out",
)


def stage_saradc_library(source: Path, destination: Path) -> dict[str, object]:
    """Copy the full saradc library and prove the initial copy is identical."""
    if not source.is_dir():
        raise EnvironmentError(f"M1 source library is missing: {source}")
    before = tree_identity((source,))
    shutil.copytree(source, destination, copy_function=shutil.copy2)
    staged = tree_identity((destination,))
    identity_matches = all(
        before[key] == staged[key]
        for key in ("file_count", "byte_count", "identity_sha256")
    )
    if not identity_matches:
        raise EnvironmentError("scratch saradc library does not match its source")
    return {
        "source": str(source),
        "destination": str(destination),
        "source_identity": before,
        "initial_staged_identity": staged,
        "initial_copy_matches": True,
    }


def relocate_maestro_project_dirs(active_state: Path, project_dir: Path) -> dict[str, object]:
    """Relocate copied Maestro per-test projectDir fields into the run scratch."""
    text = active_state.read_text(encoding="utf-8")
    old_values: list[str] = []

    def replace(match: re.Match[str]) -> str:
        old = match.group(2)
        old_values.append(old)
        marker = "/saradc/"
        if marker not in old:
            raise EnvironmentError(f"cannot relocate unexpected Maestro projectDir: {old}")
        suffix = old.split(marker, 1)[1]
        return f'{match.group(1)}{project_dir}/saradc/{suffix}{match.group(3)}'

    relocated = _PROJECT_DIR.sub(replace, text)
    if len(old_values) != 2:
        raise EnvironmentError(
            f"expected two Maestro projectDir fields, found {len(old_values)}"
        )
    active_state.write_text(relocated, encoding="utf-8")
    new_values = [match.group(2) for match in _PROJECT_DIR.finditer(relocated)]
    if not all(Path(value).is_relative_to(project_dir) for value in new_values):
        raise EnvironmentError("one or more relocated Maestro paths escape the run scratch")
    return {"path": str(active_state), "old": old_values, "new": new_values}


def write_scratch_cds_lib(path: Path, source_cds_lib: Path, scratch_saradc: Path) -> None:
    text = (
        f"INCLUDE {source_cds_lib}\n"
        "UNDEFINE saradc\n"
        f"DEFINE saradc {scratch_saradc}\n"
    )
    path.write_text(text, encoding="utf-8")


def load_json_artifact(path: Path, *, provider: str) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise EnvironmentError(f"required M1 artifact is missing or empty: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise EnvironmentError(f"invalid M1 artifact schema: {path}")
    if payload.get("provider") != provider:
        raise EnvironmentError(f"unexpected M1 artifact provider: {path}")
    if payload.get("scratch_only") is not True:
        raise EnvironmentError(f"M1 artifact is not marked scratch-only: {path}")
    return payload


def parse_nominal_csv(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise EnvironmentError(f"nominal detail CSV is missing or empty: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        header = next(index for index, line in enumerate(lines) if line.startswith("Test,Output,"))
    except StopIteration as exc:
        raise EnvironmentError("nominal detail CSV has no Test/Output table") from exc
    rows = list(csv.DictReader(lines[header:]))
    row = next(
        (
            value
            for value in rows
            if value.get("Test") == "saradc:comparator_offset_TB_new:1"
            and value.get("Output") == "Offset"
        ),
        None,
    )
    if row is None:
        raise EnvironmentError("nominal detail CSV has no comparator Offset result")
    raw_value = str(row.get("Nominal", "")).strip()
    verdict = str(row.get("Pass/Fail", "")).strip().lower()
    if raw_value in {"", "netl err", "sim err", "eval err"}:
        raise EnvironmentError(f"nominal comparator Offset is unavailable: {raw_value!r}")
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise EnvironmentError(f"nominal comparator Offset is not numeric: {raw_value}") from exc
    return {
        "test": row["Test"],
        "output": row["Output"],
        "raw_value": raw_value,
        "value_volts": value,
        "spec": str(row.get("Spec", "")),
        "verdict": verdict,
        "passes": verdict == "pass" and -5e-3 <= value <= 5e-3,
    }


def find_nominal_evidence(results: Path, history: str) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", history):
        raise EnvironmentError(f"unsafe nominal history name: {history!r}")
    root = results / history
    if not root.is_dir():
        raise EnvironmentError(f"nominal history results are missing: {root}")
    point_dirs = sorted(
        path for path in root.iterdir() if path.is_dir() and path.name.isdecimal()
    )
    if len(point_dirs) != 1:
        raise EnvironmentError(
            "nominal run did not produce exactly one numeric ADE point directory"
        )
    point = point_dirs[0] / "saradc:comparator_offset_TB_new:1"
    artifacts = (
        point / "psf" / "spectre.out",
        point / "netlist" / "netlist",
        point / "netlist" / "runSimulation",
    )
    if not all(path.is_file() for path in artifacts):
        raise EnvironmentError(
            "canonical nominal ADE point is missing its Spectre log, netlist, or launcher"
        )
    results_root = results.resolve()
    if not all(path.resolve().is_relative_to(results_root) for path in artifacts):
        raise EnvironmentError("canonical nominal evidence escapes the results root")
    return artifacts


def validate_spectre_and_netlist(
    spectre_out: Path,
    netlist: Path,
    launcher: Path,
    *,
    expected_version: str,
) -> dict[str, object]:
    spectre_text = spectre_out.read_text(encoding="utf-8", errors="replace")
    match = _SPECTRE_VERSION.search(spectre_text)
    version = match.group("version") if match else ""
    netlist_text = netlist.read_text(encoding="utf-8", errors="replace")
    checks: Mapping[str, bool] = {
        "spectre_version_matches": version == expected_version,
        "spectre_zero_errors": "spectre completes with 0 errors" in spectre_text,
        "target_subckt_present": "// Cell name: comparator_new" in netlist_text
        and "subckt comparator_new " in netlist_text,
        "top_instance_uses_target": bool(
            re.search(r"^I0\s+\([^\n]+\)\s+comparator_new$", netlist_text, re.MULTILINE)
        ),
        "launcher_uses_path_resolved_spectre": launcher.read_text(
            encoding="utf-8", errors="replace"
        ).lstrip().startswith("spectre "),
    }
    return {
        "checks": dict(checks),
        "all_checks_pass": all(checks.values()),
        "license_blocked": any(marker in spectre_text for marker in _LICENSE_FAILURES),
        "spectre_version": version,
        "spectre_out": str(spectre_out),
        "netlist": str(netlist),
        "launcher": str(launcher),
    }


def collect_nominal_result_evidence(
    results: Path,
    history: str,
    detail_csv: Path,
    *,
    expected_version: str,
) -> dict[str, object]:
    spectre_out, netlist, launcher = find_nominal_evidence(results, history)
    runtime = validate_spectre_and_netlist(
        spectre_out,
        netlist,
        launcher,
        expected_version=expected_version,
    )
    metric = None if runtime["license_blocked"] else parse_nominal_csv(detail_csv)
    return {"metric": metric, "runtime": runtime}


__all__ = [
    "find_nominal_evidence",
    "collect_nominal_result_evidence",
    "load_json_artifact",
    "parse_nominal_csv",
    "relocate_maestro_project_dirs",
    "stage_saradc_library",
    "validate_spectre_and_netlist",
    "write_scratch_cds_lib",
]
