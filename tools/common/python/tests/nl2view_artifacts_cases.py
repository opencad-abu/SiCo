"""nl2view artifacts behavior cases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


from nl2view_fixtures import (
    ENTRY,
    _run,
    _write_fake_cds_text_to_5x,
)

def test_cds_lib_undefine_overrides_included_library(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    definitions = tmp_path / "definitions"
    included_library = definitions / "included" / "work"
    included_library.mkdir(parents=True)
    (definitions / "included.lib").write_text(
        "DEFINE work ./included/work\n", encoding="utf-8"
    )
    (tmp_path / "cds.lib").write_text(
        "INCLUDE ./definitions/included.lib\nUNDEFINE work\n", encoding="utf-8"
    )

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={
            "NL2VIEW_TEST_LIBRARY_PATH": str(included_library),
        },
    )

    assert payload is None
    assert completed.returncode == 2
    assert "library 'work' is not defined" in completed.stderr

def test_overwrites_existing_view_even_with_same_second_timestamps(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.sp"
    source.write_text("* replacement netlist\nR1 a b 2k\n", encoding="utf-8")
    library = tmp_path / "work"
    view = library / "top" / "spiceText"
    view.mkdir(parents=True)
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (view / "spice.spc").write_text("* old netlist\nR1 a b 1k\n", encoding="utf-8")
    (view / "master.tag").write_text(
        "-- Master.tag File, Rev:1.0\nspice.spc\n", encoding="utf-8"
    )
    (view / "netlist.oa").write_bytes(b"old OA connectivity")
    timestamp_ns = 1_700_000_000_000_000_000
    for artifact in view.iterdir():
        os.utime(artifact, ns=(timestamp_ns, timestamp_ns))

    completed, payload = _run(
        tmp_path,
        "--format",
        "spice",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert (view / "spice.spc").read_bytes() == source.read_bytes()

def test_detects_same_size_content_change_with_preserved_timestamps(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.sp"
    source.write_text("* new netlist\nR1 a b 2k\n", encoding="utf-8")
    library = tmp_path / "work"
    view = library / "top" / "spiceText"
    view.mkdir(parents=True)
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (view / "spice.spc").write_text(
        "* old netlist\nR1 a b 1k\n", encoding="utf-8"
    )
    (view / "master.tag").write_text(
        "-- Master.tag File, Rev:1.0\nspice.spc\n", encoding="utf-8"
    )
    (view / "netlist.oa").write_bytes(b"fake OA connectivity old payload")
    timestamp_ns = 1_700_000_000_000_000_000
    metadata = {}
    for artifact in view.iterdir():
        os.utime(artifact, ns=(timestamp_ns, timestamp_ns))
        metadata[artifact.name] = [timestamp_ns, timestamp_ns]

    completed, payload = _run(
        tmp_path,
        "--format",
        "spice",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={
            "NL2VIEW_TEST_PRESERVE_METADATA": json.dumps(metadata),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert (view / "spice.spc").read_bytes() == source.read_bytes()

def test_reports_missing_cds_text_to_5x_before_subprocess_start(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment["CAD_PYTHON"] = sys.executable
    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(tmp_path / "missing-cdsTextTo5x"),
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 127
    assert "program not found" in completed.stderr
    assert "Traceback" not in completed.stderr

def test_reports_cds_lib_debug_start_failure_as_resolver_error(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    importer = _write_fake_cds_text_to_5x(tmp_path / "cdsTextTo5x")
    resolver = tmp_path / "cdsLibDebug"
    resolver.write_text("#!/no/such/interpreter\n", encoding="utf-8")
    resolver.chmod(0o755)
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_RECORD": str(tmp_path / "record.json"),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(importer),
            "--cds-lib-debug",
            str(resolver),
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "cannot execute cdsLibDebug" in completed.stderr
    assert str(resolver) in completed.stderr
    assert str(importer) not in completed.stderr

def test_uses_configured_sico_temp_directory(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    configured = tmp_path / "launch" / ".sico"
    configured.parent.mkdir()
    configured.mkdir(mode=0o700)

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={"SICO_TEMP_DIR": str(configured)},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["cwd"] == str(configured)
    assert set(payload["temp"].values()) == {None}

def test_rejects_unknown_library_before_starting_cadence(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "typo",
        "--cell",
        "top",
        str(source),
    )

    assert completed.returncode == 2
    assert payload is None
    assert "library 'typo' is not defined" in completed.stderr

def test_executes_resolved_tilde_program_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write_fake_cds_text_to_5x(home / "cdsTextTo5x")
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    record = tmp_path / "record.json"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "HOME": str(home),
            "NL2VIEW_TEST_RECORD": str(record),
            "NL2VIEW_TEST_LIBRARY_PATH": str(tmp_path / "work"),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            "~/cdsTextTo5x",
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(record.read_text(encoding="utf-8"))["argv"]
