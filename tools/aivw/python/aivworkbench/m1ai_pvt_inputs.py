"""Validate the M1 comparator default-plus-45-PVT Spectre evidence."""

from __future__ import annotations

from itertools import product
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from .errors import EnvironmentError


NOISE_TEST = "saradc:comparator_noise_TB_new:1"
OFFSET_TEST = "saradc:comparator_offset_TB_new:1"
EXPECTED_TESTS = (NOISE_TEST, OFFSET_TEST)
_SAFE_HISTORY = re.compile(r"[A-Za-z0-9_. -]+")
_VERSION = re.compile(r"^Version\s+(?P<version>\S+)", re.MULTILINE)
_MODEL_SECTION = re.compile(r'Section="(?P<section>[^"]+)"')
_LICENSE_FAILURES = (
    "FATAL (SPECTRE-209)",
    "required license could not be checked out",
)


def history_rdb_path(setup_db_dir: Path, history: str) -> Path:
    """Return the URL-encoded Maestro result DB path for a safe history name."""
    if not _SAFE_HISTORY.fullmatch(history):
        raise EnvironmentError(f"unsafe PVT history name: {history!r}")
    return setup_db_dir / "results" / "maestro" / f"{quote(history, safe='')}.rdb"


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.stat().st_size == 0:
        raise EnvironmentError(f"PVT Maestro result DB is missing or empty: {path}")
    uri = f"file:{quote(str(path), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _parameter_values(
    connection: sqlite3.Connection,
) -> tuple[dict[int, str], dict[int, dict[str, object]]]:
    parameters = {
        int(parameter_id): str(name)
        for parameter_id, name in connection.execute("SELECT parameterID,name FROM parameter")
    }
    values: dict[int, dict[str, object]] = {}
    for point_id, parameter_id, value in connection.execute(
        "SELECT pointID,parameterID,value FROM parameterValue"
    ):
        name = parameters.get(int(parameter_id))
        if name:
            values.setdefault(int(point_id), {})[name] = value
    return parameters, values


def _process_section(value: object) -> str:
    match = _MODEL_SECTION.search(str(value or ""))
    return match.group("section") if match else ""


def _numeric_results(
    connection: sqlite3.Connection,
    test: str,
    result: str,
) -> dict[int, float]:
    rows = connection.execute(
        "SELECT rv.pointID,rv.value,rv.errorID "
        "FROM resultValue rv "
        "JOIN result r ON r.resultID=rv.resultID "
        "JOIN test t ON t.testID=r.testID "
        "WHERE t.name=? AND r.name=?",
        (test, result),
    )
    values: dict[int, float] = {}
    for point_id, value, error_id in rows:
        if error_id is not None or not isinstance(value, (int, float)):
            continue
        values[int(point_id)] = float(value)
    return values


def _specifications(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, str, str]]:
    return {
        (str(test), str(result)): (str(kind), str(first or ""), str(second or ""))
        for test, result, kind, first, second in connection.execute(
            "SELECT t.name,r.name,s.type,s.expression1,s.expression2 "
            "FROM spec s "
            "JOIN result r ON r.resultID=s.resultID "
            "JOIN test t ON t.testID=r.testID"
        )
    }


def parse_pvt_rdb(
    path: Path,
    *,
    supplies: Iterable[str] = ("1.15", "1.2", "1.25"),
    temperatures: Iterable[str] = ("0", "27", "80"),
    processes: Iterable[str] = ("tt", "ff", "ss", "sf", "fs"),
) -> dict[str, object]:
    """Parse and fail-closed validate one new Maestro PVT result database."""
    expected_supplies = tuple(float(value) for value in supplies)
    expected_temperatures = tuple(float(value) for value in temperatures)
    expected_processes = tuple(str(value) for value in processes)
    expected_matrix = set(product(expected_supplies, expected_temperatures, expected_processes))
    connection = _open_read_only(path)
    try:
        required_tables = {
            "corner", "parameter", "parameterValue", "point", "result", "resultValue",
            "spec", "test", "testStatus",
        }
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not required_tables <= tables:
            raise EnvironmentError(
                f"PVT Maestro result DB is missing tables: {sorted(required_tables - tables)}"
            )

        tests = {
            int(test_id): str(name)
            for test_id, name in connection.execute("SELECT testID,name FROM test")
        }
        corners = {
            int(corner_id): str(name or "")
            for corner_id, name in connection.execute("SELECT cornerID,name FROM corner")
        }
        _, parameter_values = _parameter_values(connection)
        points: list[dict[str, object]] = []
        pvt_matrix: set[tuple[float, float, str]] = set()
        default_points = 0
        point_ids: set[int] = set()
        for point_id, corner_id in connection.execute(
            "SELECT pointID,cornerID FROM point ORDER BY pointID"
        ):
            point_id = int(point_id)
            point_ids.add(point_id)
            corner = corners.get(int(corner_id), "")
            values = parameter_values.get(point_id, {})
            supply_raw = values.get("VDD", "")
            temperature_raw = values.get("temperature", "")
            process_name = _process_section(values.get("corModelSpec", ""))
            if corner == "":
                default_points += 1
            else:
                try:
                    triple = (float(supply_raw), float(temperature_raw), process_name)
                except (TypeError, ValueError) as exc:
                    raise EnvironmentError(
                        f"PVT point {point_id} has non-numeric VDD/temperature"
                    ) from exc
                pvt_matrix.add(triple)
            points.append(
                {
                    "point_id": point_id,
                    "corner": corner or "_default",
                    "VDD": str(supply_raw),
                    "temperature": str(temperature_raw),
                    "process": process_name,
                }
            )

        statuses = list(
            connection.execute("SELECT pointID,testID,statusCode,errorID FROM testStatus")
        )
        complete_pairs = {
            (int(point_id), int(test_id))
            for point_id, test_id, status, _error_id in statuses
            if int(status) == 3
        }
        expected_pairs = set(product(point_ids, tests))
        offset = _numeric_results(connection, OFFSET_TEST, "Offset")
        noise = _numeric_results(connection, NOISE_TEST, "InputReferredNoise")
        specs = _specifications(connection)
        checks: Mapping[str, bool] = {
            "tests_match": set(tests.values()) == set(EXPECTED_TESTS),
            "point_count": len(point_ids) == 46,
            "one_default_point": default_points == 1,
            "pvt_matrix_matches": pvt_matrix == expected_matrix,
            "all_test_statuses_complete": complete_pairs == expected_pairs,
            "offset_rows_complete": set(offset) == point_ids,
            "noise_rows_complete": set(noise) == point_ids,
            "offset_spec_matches": specs.get((OFFSET_TEST, "Offset"))
            == ("range", "-5m", "5m"),
            "noise_spec_matches": specs.get((NOISE_TEST, "InputReferredNoise"))
            == ("lt", "700u", ""),
            "all_offsets_pass": len(offset) == 46
            and all(-5e-3 <= value <= 5e-3 for value in offset.values()),
            "all_noise_pass": len(noise) == 46
            and all(value < 700e-6 for value in noise.values()),
        }
        offset_worst = max(offset, key=lambda key: abs(offset[key])) if offset else None
        noise_worst = max(noise, key=noise.__getitem__) if noise else None
        point_by_id = {int(point["point_id"]): point for point in points}
        return {
            "rdb": str(path),
            "checks": dict(checks),
            "all_checks_pass": all(checks.values()),
            "point_count": len(point_ids),
            "test_status_count": len(statuses),
            "points": points,
            "metrics": {
                "Offset": {
                    "minimum_volts": min(offset.values()) if offset else None,
                    "maximum_volts": max(offset.values()) if offset else None,
                    "maximum_absolute_volts": max(map(abs, offset.values())) if offset else None,
                    "worst_point": point_by_id.get(offset_worst) if offset_worst else None,
                    "passes": checks["all_offsets_pass"],
                },
                "InputReferredNoise": {
                    "minimum_volts_rms": min(noise.values()) if noise else None,
                    "maximum_volts_rms": max(noise.values()) if noise else None,
                    "worst_point": point_by_id.get(noise_worst) if noise_worst else None,
                    "passes": checks["all_noise_pass"],
                },
            },
        }
    except sqlite3.DatabaseError as exc:
        raise EnvironmentError(f"cannot parse PVT Maestro result DB: {exc}") from exc
    finally:
        connection.close()


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def collect_pvt_runtime(
    results: Path,
    history: str,
    *,
    expected_version: str,
) -> dict[str, object]:
    """Validate canonical per-test runtime artifacts under the numeric ADE point."""
    if not _SAFE_HISTORY.fullmatch(history):
        raise EnvironmentError(f"unsafe PVT history name: {history!r}")
    root = results / history
    if not root.is_dir():
        raise EnvironmentError(f"PVT history results are missing: {root}")
    numeric = sorted(path for path in root.iterdir() if path.is_dir() and path.name.isdecimal())
    if {path.name for path in numeric} != {str(value) for value in range(1, 47)}:
        raise EnvironmentError("PVT history did not produce numeric design points 1 through 46")

    tests: dict[str, dict[str, Any]] = {}
    overall = True
    license_blocked = False
    for test in EXPECTED_TESTS:
        test_roots = [point / test for point in numeric]
        spectre_logs = sorted(
            path for test_root in test_roots for path in test_root.rglob("spectre.out")
        )
        netlists = sorted(
            path
            for test_root in test_roots
            for path in test_root.rglob("netlist")
            if path.is_file() and path.parent.name == "netlist"
        )
        launchers = sorted(
            path for test_root in test_roots for path in test_root.rglob("runSimulation")
        )
        artifacts = [*spectre_logs, *netlists, *launchers]
        if not artifacts or not all(_inside(path, results) for path in artifacts):
            raise EnvironmentError(f"PVT runtime artifacts are missing or unsafe for {test}")

        log_checks: list[bool] = []
        versions: set[str] = set()
        for path in spectre_logs:
            text = path.read_text(encoding="utf-8", errors="replace")
            match = _VERSION.search(text)
            version = match.group("version") if match else ""
            versions.add(version)
            log_checks.append(
                version == expected_version and "spectre completes with 0 errors" in text
            )
            license_blocked = license_blocked or any(
                marker in text for marker in _LICENSE_FAILURES
            )
        netlist_checks = []
        target_instance = re.compile(r"^I\S*\s+\([^\n]+\)\s+comparator_new$", re.MULTILINE)
        for path in netlists:
            text = path.read_text(encoding="utf-8", errors="replace")
            netlist_checks.append(
                "// Cell name: comparator_new" in text
                and "subckt comparator_new " in text
                and bool(target_instance.search(text))
            )
        launcher_checks = [
            path.read_text(encoding="utf-8", errors="replace")
            .lstrip()
            .startswith("spectre ")
            for path in launchers
        ]
        checks = {
            "spectre_logs_complete": len(spectre_logs) == 46,
            "spectre_logs_pass": len(log_checks) == 46 and all(log_checks),
            "spectre_version_matches": versions == {expected_version},
            "netlists_complete": len(netlists) == 46,
            "all_netlists_use_target": len(netlist_checks) == 46
            and all(netlist_checks),
            "launchers_complete": len(launchers) == 46,
            "all_launchers_use_path_resolved_spectre": len(launcher_checks) == 46
            and all(launcher_checks),
        }
        passed = all(checks.values())
        overall = overall and passed
        tests[test] = {
            "checks": checks,
            "all_checks_pass": passed,
            "spectre_log_count": len(spectre_logs),
            "netlist_count": len(netlists),
            "launcher_count": len(launchers),
            "spectre_logs": [str(path) for path in spectre_logs],
            "netlists": [str(path) for path in netlists],
            "launchers": [str(path) for path in launchers],
        }
    return {
        "history_root": str(root),
        "numeric_design_points": [path.name for path in numeric],
        "tests": tests,
        "all_checks_pass": overall,
        "license_blocked": license_blocked,
    }


def runtime_artifact_paths(runtime: Mapping[str, Any]) -> list[Path]:
    """Return the bounded text-artifact set referenced by runtime evidence."""
    paths: list[Path] = []
    for evidence in runtime.get("tests", {}).values():
        for key in ("spectre_logs", "netlists", "launchers"):
            paths.extend(Path(str(path)) for path in evidence.get(key, []))
    return paths


__all__ = [
    "EXPECTED_TESTS",
    "NOISE_TEST",
    "OFFSET_TEST",
    "collect_pvt_runtime",
    "history_rdb_path",
    "parse_pvt_rdb",
    "runtime_artifact_paths",
]
