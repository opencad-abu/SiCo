"""nl2view cli behavior cases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from cadview.nl2view import ImportRequest, run_import
from nl2view_fixtures import (
    _run,
    _write_fake_cds_text_to_5x,
)

@pytest.mark.parametrize(
    ("netlist_format", "language", "default_view"),
    (
        ("spectre", "spectre", "spectreText"),
        ("hspice", "hspice", "hspiceText"),
        ("spice", "spice", "spiceText"),
        ("dspf", "dspf", "dspfText"),
    ),
)
def test_format_mapping_and_default_copy_mode(
    tmp_path: Path,
    netlist_format: str,
    language: str,
    default_view: str,
) -> None:
    source = tmp_path / "top.sp"
    source.write_text("* netlist\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        netlist_format,
        "--library",
        "work",
        "--cell",
        "top",
        "--cdslib",
        str(cds_lib),
        str(source),
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["argv"] == [
        "-LIB",
        "work",
        "-CELL",
        "top",
        "-VIEW",
        default_view,
        "-CDSLIB",
        str(cds_lib.resolve()),
        "-LANG",
        language,
        str(source.resolve()),
    ]
    assert payload["ld_library_path"] == ""
    assert payload["nolink"] == "1"
    expected_temp = str(tmp_path / ".sico")
    assert payload["cwd"] == expected_temp
    assert set(payload["temp"].values()) == {None}
    assert completed.stdout == "fake cdsTextTo5x stdout\n"
    assert completed.stderr == "fake cdsTextTo5x stderr\n"

def test_import_can_run_from_gui_worker_thread(tmp_path: Path) -> None:
    """The GUI publishes from a worker thread, where signal.signal is illegal."""

    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    fake = _write_fake_cds_text_to_5x(tmp_path / "cdsTextTo5x")
    record = tmp_path / "record.json"
    temporary = tmp_path / ".sico"
    log = tmp_path / "nl2view.log"
    environment = {
        "SICO_TEMP_DIR": str(temporary),
        "NL2VIEW_TEST_RECORD": str(record),
        "NL2VIEW_TEST_LIBRARY_PATH": str(tmp_path / "work"),
        "CAD5X_NOLINK": "",
    }
    request = ImportRequest(
        source=source,
        netlist_format="spectre",
        library="work",
        cell="top",
        view="spectreText",
        cds_library_file=cds_lib,
        log_file=log,
        executable=str(fake),
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run_import,
            request,
            environment,
            timeout=10.0,
        )
        assert future.result(timeout=20) == 0

    assert (tmp_path / "work" / "top" / "spectreText" / "netlist.oa").is_file()
    evidence = log.read_text(encoding="utf-8")
    assert "--- nl2view wrapper diagnostics ---" in evidence
    assert "outcome: succeeded" in evidence
    assert "fake cdsTextTo5x stdout" in evidence
    assert "validated OA text view:" in evidence

def test_link_mode_customizes_optional_view_arguments(tmp_path: Path) -> None:
    source = tmp_path / "amp netlist.sp"
    source.write_text("* netlist\n", encoding="utf-8")
    cds_lib = tmp_path / "project cds.lib"
    cds_lib.write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    log = tmp_path / "import log.txt"

    completed, payload = _run(
        tmp_path,
        "--format",
        "hspice",
        "--library",
        "work",
        "--cell",
        "amp",
        "--view",
        "hspiceImported",
        "--log",
        str(log),
        "--cdslib",
        str(cds_lib),
        "--link",
        str(source),
        extra_environment={"CDS5X_NOLINK": "1"},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert payload["argv"] == [
        "-LIB",
        "work",
        "-CELL",
        "amp",
        "-VIEW",
        "hspiceImported",
        "-CDSLIB",
        str(cds_lib.resolve()),
        "-LANG",
        "hspice",
        "-LOG",
        str(log.resolve()),
        str(source.resolve()),
    ]
    assert payload["ld_library_path"] == ""
    assert payload["nolink"] is None

def test_copy_mode_replaces_existing_link_to_same_source(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    view = library / "top" / "spectreText"
    view.mkdir(parents=True)
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    linked_master = view / "spectre.spectre"
    linked_master.symlink_to(source)
    (view / "master.tag").write_text(
        "-- Master.tag File, Rev:1.0\nspectre.spectre\n", encoding="utf-8"
    )
    (view / "netlist.oa").write_bytes(b"old OA connectivity")

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        "--copy",
        str(source),
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    invocation_source = Path(payload["argv"][-1])
    assert invocation_source.parent == tmp_path / ".sico"
    assert invocation_source.name.startswith("nl2view-copy-")
    assert not invocation_source.exists()
    assert not linked_master.is_symlink()
    assert linked_master.read_bytes() == source.read_bytes()

def test_defaults_cds_library_file_from_working_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "adc_top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "adc_top",
        str(source),
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    arguments = payload["argv"]
    assert arguments[arguments.index("-CELL") + 1] == "adc_top"
    assert arguments[arguments.index("-CDSLIB") + 1] == str(
        (tmp_path / "cds.lib").resolve()
    )

def test_requires_explicit_cell_before_starting_cadence(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        str(source),
    )

    assert completed.returncode == 2
    assert payload is None
    assert "the following arguments are required: --cell" in completed.stderr

def test_rejects_pspice_before_starting_cadence(tmp_path: Path) -> None:
    source = tmp_path / "top.cir"
    source.write_text("* netlist\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "pspice",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
    )

    assert completed.returncode == 2
    assert payload is None
    assert "invalid choice: 'pspice'" in completed.stderr

def test_accepts_generic_spice_as_hspice_compatible_input(tmp_path: Path) -> None:
    source = tmp_path / "top.sp"
    source.write_text("* netlist\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

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
    arguments = payload["argv"]
    assert arguments[arguments.index("-LANG") + 1] == "spice"
    assert arguments[arguments.index("-VIEW") + 1] == "spiceText"

@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        (
            ("--format", "spectre", "--library", "bad lib", "--cell", "top", "top.scs"),
            "invalid library name",
        ),
        (
            ("--format", "spectre", "--library", "../bad", "--cell", "top", "top.scs"),
            "invalid library name",
        ),
        (
            ("--format", "spectre", "--library", ".", "--cell", "top", "top.scs"),
            "invalid library name",
        ),
        (
            (
                "--format",
                "spectre",
                "--library",
                "work",
                "--cell",
                "missing",
                "missing.scs",
            ),
            "cannot access netlist file",
        ),
        (
            (
                "--format",
                "spectre",
                "--library",
                "work",
                "--cell",
                "empty",
                "empty.scs",
            ),
            "netlist file is empty",
        ),
        (
            ("--format", "spectre", "--library", "work", "--cell", "bad cell", "top.scs"),
            "invalid cell name",
        ),
        (
            (
                "--format",
                "spectre",
                "--library",
                "work",
                "--cell",
                "top",
                "--cdslib",
                "missing.lib",
                "top.scs",
            ),
            "cannot access cds.lib",
        ),
    ),
)
def test_validates_inputs_before_starting_cadence(
    tmp_path: Path,
    arguments: tuple[str, ...],
    message: str,
) -> None:
    (tmp_path / "top.scs").write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "empty.scs").touch()
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(tmp_path, *arguments)

    assert completed.returncode == 2
    assert payload is None
    assert message in completed.stderr

def test_preserves_cadence_exit_code_and_diagnostics(tmp_path: Path) -> None:
    source = tmp_path / "top.dspf"
    source.write_text("*|DSPF 1.0\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "dspf",
        "--library",
        "work",
        "--cell",
        "top",
        str(source),
        extra_environment={"NL2VIEW_TEST_EXIT": "17"},
    )

    assert payload is not None
    assert completed.returncode == 17
    assert completed.stdout == "fake cdsTextTo5x stdout\n"
    assert completed.stderr == "fake cdsTextTo5x stderr\n"
