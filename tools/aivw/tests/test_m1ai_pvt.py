from __future__ import annotations

from itertools import product
from pathlib import Path
import sqlite3

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.m1ai_pvt_inputs import (
    EXPECTED_TESTS,
    NOISE_TEST,
    OFFSET_TEST,
    collect_pvt_runtime,
    history_rdb_path,
    parse_pvt_rdb,
)


def _pvt_rdb(path: Path, *, failing_noise: bool = False) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE corner(cornerID INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE parameter(
          parameterID INTEGER PRIMARY KEY, name TEXT, defaultValue TEXT, fixed TEXT,
          testID INTEGER, lib TEXT, cell TEXT, view TEXT, instance TEXT, type TEXT,
          expression TEXT, immutable TEXT, hasLocal TEXT, useReference TEXT
        );
        CREATE TABLE parameterValue(pointID INTEGER, parameterID INTEGER, value NUMERIC);
        CREATE TABLE point(
          pointID INTEGER PRIMARY KEY, designPointNumber INTEGER, merit FLOAT,
          cornerID INTEGER, groupID INTEGER
        );
        CREATE TABLE test(testID INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE result(
          resultID INTEGER PRIMARY KEY, name TEXT, testID INTEGER, type TEXT,
          expression TEXT, resultOrder INTEGER, visible TEXT
        );
        CREATE TABLE resultValue(
          pointID INTEGER, resultID INTEGER, value BLOB, errorID INTEGER
        );
        CREATE TABLE spec(
          resultID INTEGER, type TEXT, expression1 TEXT, expression2 TEXT
        );
        CREATE TABLE testStatus(
          pointID INTEGER, testID INTEGER, statusCode INTEGER, errorID INTEGER
        );
        """
    )
    connection.executemany(
        "INSERT INTO parameter VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (1, "temperature", "27", "false", "", "", "", "", "", "desVar", None, "false", "false", "false"),
            (2, "VDD", "1.2", "false", "", "", "", "", "", "desVar", None, "false", "false", "false"),
            (3, "corModelSpec", "none", "false", "", "", "", "", "", "modelFile", None, "false", "false", "false"),
        ),
    )
    connection.executemany(
        "INSERT INTO test VALUES (?,?)",
        ((1, NOISE_TEST), (2, OFFSET_TEST)),
    )
    connection.executemany(
        "INSERT INTO result VALUES (?,?,?,?,?,?,?)",
        ((1, "InputReferredNoise", 1, "0", "expr", 1, "1"),
         (2, "Offset", 2, "0", "expr", 2, "1")),
    )
    connection.executemany(
        "INSERT INTO spec VALUES (?,?,?,?)",
        ((1, "lt", "700u", ""), (2, "range", "-5m", "5m")),
    )
    connection.execute("INSERT INTO corner VALUES (?,?)", (46, ""))
    connection.execute("INSERT INTO point VALUES (?,?,?,?,?)", (1, 1, 1.0, 46, None))
    for test_id in (1, 2):
        connection.execute("INSERT INTO testStatus VALUES (?,?,?,?)", (1, test_id, 3, None))
    connection.execute(
        "INSERT INTO resultValue VALUES (?,?,?,?)",
        (1, 1, 701e-6 if failing_noise else 500e-6, None),
    )
    connection.execute("INSERT INTO resultValue VALUES (?,?,?,?)", (1, 2, 0.0, None))

    combinations = list(product((1.15, 1.2, 1.25), (0.0, 27.0, 80.0), ("tt", "ff", "ss", "sf", "fs")))
    for index, (supply, temperature, process_name) in enumerate(combinations, start=2):
        corner_id = index - 1
        connection.execute("INSERT INTO corner VALUES (?,?)", (corner_id, f"C1_{corner_id - 1}"))
        connection.execute(
            "INSERT INTO point VALUES (?,?,?,?,?)", (index, 1, 1.0, corner_id, None)
        )
        model = f'File=/models/gpdk045.scs Section="{process_name}";'
        connection.executemany(
            "INSERT INTO parameterValue VALUES (?,?,?)",
            ((index, 1, temperature), (index, 2, supply), (index, 3, model)),
        )
        for test_id in (1, 2):
            connection.execute(
                "INSERT INTO testStatus VALUES (?,?,?,?)", (index, test_id, 3, None)
            )
        connection.execute(
            "INSERT INTO resultValue VALUES (?,?,?,?)", (index, 1, 600e-6, None)
        )
        connection.execute(
            "INSERT INTO resultValue VALUES (?,?,?,?)", (index, 2, 1e-3, None)
        )
    connection.commit()
    connection.close()


def test_pvt_rdb_requires_complete_matrix_statuses_specs_and_metrics(tmp_path: Path) -> None:
    path = tmp_path / "Interactive.0.rdb"
    _pvt_rdb(path)
    evidence = parse_pvt_rdb(path)
    assert evidence["all_checks_pass"] is True
    assert evidence["point_count"] == 46
    assert evidence["test_status_count"] == 92
    assert evidence["metrics"]["Offset"]["passes"] is True
    assert evidence["metrics"]["InputReferredNoise"]["passes"] is True


def test_pvt_rdb_fails_closed_on_one_out_of_spec_point(tmp_path: Path) -> None:
    path = tmp_path / "Interactive.0.rdb"
    _pvt_rdb(path, failing_noise=True)
    evidence = parse_pvt_rdb(path)
    assert evidence["all_checks_pass"] is False
    assert evidence["checks"]["all_noise_pass"] is False


def test_pvt_runtime_requires_all_46_numeric_design_points(tmp_path: Path) -> None:
    results = tmp_path / "results"
    for container in tuple(str(value) for value in range(1, 47)) + ("psf",):
        for test in EXPECTED_TESTS:
            root = results / "Interactive.0" / container / test / "C1_0"
            (root / "psf").mkdir(parents=True)
            (root / "netlist").mkdir()
            (root / "psf" / "spectre.out").write_text(
                "Version 23.1.0.538.isr10\nspectre completes with 0 errors\n",
                encoding="utf-8",
            )
            (root / "netlist" / "netlist").write_text(
                "// Cell name: comparator_new\n"
                "subckt comparator_new a b c d e\n"
                "ends comparator_new\n"
                "I9 (a b c d e) comparator_new\n",
                encoding="utf-8",
            )
            (root / "netlist" / "runSimulation").write_text(
                "spectre input.scs\n", encoding="utf-8"
            )

    evidence = collect_pvt_runtime(
        results, "Interactive.0", expected_version="23.1.0.538.isr10"
    )
    assert evidence["all_checks_pass"] is True
    assert len(evidence["numeric_design_points"]) == 46
    assert all(
        test["spectre_log_count"] == 46 for test in evidence["tests"].values()
    )

    missing = results / "Interactive.0" / "46"
    for path in sorted(missing.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    missing.rmdir()
    with pytest.raises(EnvironmentError, match="design points 1 through 46"):
        collect_pvt_runtime(
            results, "Interactive.0", expected_version="23.1.0.538.isr10"
        )


def test_pvt_history_path_is_encoded_and_rejects_traversal(tmp_path: Path) -> None:
    assert history_rdb_path(tmp_path, "Corner Analysis") == (
        tmp_path / "results/maestro/Corner%20Analysis.rdb"
    )
    with pytest.raises(EnvironmentError, match="unsafe PVT history"):
        history_rdb_path(tmp_path, "../../source")
