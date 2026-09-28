"""nl2view resolver behavior cases."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nl2view_fixtures import (
    ENTRY,
    _run,
    _write_fake_cds_lib_debug,
    _write_fake_cds_text_to_5x,
)

def test_does_not_ignore_generic_gdm_contact_error_without_plugin_marker(
    tmp_path: Path,
) -> None:
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
        "--cell",
        "top",
        str(source),
        extra_environment={
            "NL2VIEW_TEST_DIAGNOSTIC": (
                "Error (gdmLoadSharedLib): Contact the owner of the library to resolve the problem"
            )
        },
    )

    assert payload is not None
    assert completed.returncode == 1

def test_rejects_zero_exit_without_a_created_view(tmp_path: Path) -> None:
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
        "--cell",
        "top",
        str(source),
        extra_environment={"NL2VIEW_TEST_NO_ARTIFACT": "1"},
    )

    assert payload is not None
    assert completed.returncode == 1
    assert "did not create view" in completed.stderr

def test_rejects_undefined_library_without_modifying_cds_lib(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    original = "DEFINE work ./work\n"
    cds_lib.write_text(original, encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "newLibrary",
        "--cell",
        "top",
        str(source),
    )

    assert payload is None
    assert completed.returncode == 2
    assert "library 'newLibrary' is not defined" in completed.stderr
    assert cds_lib.read_text(encoding="utf-8") == original
    assert not (tmp_path / "newLibrary").exists()

def test_resolves_included_library_relative_to_included_file(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    definitions = tmp_path / "definitions"
    definitions.mkdir()
    library = definitions / "oa" / "work"
    library.mkdir(parents=True)
    included = definitions / "project.lib"
    included.write_text("DEFINE work ./oa/work\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text(
        "INCLUDE $NL2VIEW_TEST_DEFINITIONS/project.lib\n",
        encoding="utf-8",
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
            "NL2VIEW_TEST_DEFINITIONS": str(definitions),
            "NL2VIEW_TEST_LIBRARY_PATH": str(library),
        },
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert (library / "top" / "spectreText" / "spectre.spectre").is_file()

def test_uses_cadence_resolver_for_installation_root_expressions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "cadence-install" / "libraries" / "work"
    library.mkdir(parents=True)
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text(
        "DEFINE work $(inst_root_with:tools/dfII/bin/virtuoso)/libraries/work\n",
        encoding="utf-8",
    )
    fake_importer = _write_fake_cds_text_to_5x(
        tmp_path / "cadence/tools/dfII/bin/cdsTextTo5x"
    )
    fake_resolver = _write_fake_cds_lib_debug(
        tmp_path / "cadence/tools/bin/cdsLibDebug"
    )
    record = tmp_path / "record.json"
    resolver_record = tmp_path / "resolver.json"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_CDSLIBDEBUG_RECORD": str(resolver_record),
            "NL2VIEW_TEST_IMPORT_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_RECORD": str(record),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(fake_importer),
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            "--cdslib",
            str(cds_lib),
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(resolver_record.read_text(encoding="utf-8")) == [
        "-cdslib",
        str(cds_lib.resolve()),
        "-cla",
    ]
    assert fake_resolver.is_file()
    assert (library / "top/spectreText/spectre.spectre").is_file()

def test_finds_cadence_resolver_from_explicit_install_bin(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE work $(compute:THIS_TOOL_INST_ROOT)/work\n")
    install = tmp_path / "IC23.10.130"
    fake_importer = _write_fake_cds_text_to_5x(install / "bin/cdsTextTo5x")
    _write_fake_cds_lib_debug(install / "tools/bin/cdsLibDebug")
    record = tmp_path / "record.json"
    resolver_record = tmp_path / "resolver.json"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_CDSLIBDEBUG_RECORD": str(resolver_record),
            "NL2VIEW_TEST_IMPORT_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_RECORD": str(record),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(fake_importer),
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            "--cdslib",
            str(cds_lib),
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert resolver_record.is_file()

def test_finds_cadence_resolver_from_share_dfii_entry(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text("DEFINE work $(compute:THIS_TOOL_INST_ROOT)/work\n")
    install = tmp_path / "IC23.10.130"
    fake_importer = _write_fake_cds_text_to_5x(
        install / "share/dfII/bin/cdsTextTo5x"
    )
    _write_fake_cds_lib_debug(install / "bin/cdsLibDebug")
    resolver_record = tmp_path / "resolver.json"
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_CDSLIBDEBUG_RECORD": str(resolver_record),
            "NL2VIEW_TEST_IMPORT_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_LIBRARY_PATH": str(library),
            "NL2VIEW_TEST_RECORD": str(tmp_path / "record.json"),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(fake_importer),
            "--format",
            "spectre",
            "--library",
            "work",
            "--cell",
            "top",
            "--cdslib",
            str(cds_lib),
            str(source),
        ],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert resolver_record.is_file()

def test_rejects_library_mapping_that_differs_from_active_session(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    selected_library = tmp_path / "selected-work"
    selected_library.mkdir()
    session_library = tmp_path / "session-work"
    session_library.mkdir()
    (tmp_path / "cds.lib").write_text(
        f"DEFINE work {selected_library}\n", encoding="utf-8"
    )

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        "--expected-library-path",
        str(session_library),
        str(source),
        extra_environment={"NL2VIEW_TEST_LIBRARY_PATH": str(selected_library)},
    )

    assert completed.returncode == 2
    assert payload is None
    assert "current Virtuoso session uses" in completed.stderr

def test_accepts_library_mapping_that_matches_active_session(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(
        f"DEFINE work {library}\n", encoding="utf-8"
    )

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        "--expected-library-path",
        str(library),
        str(source),
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None

def test_installation_root_expression_requires_cadence_resolver(
    tmp_path: Path,
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    cds_lib = tmp_path / "cds.lib"
    cds_lib.write_text(
        "DEFINE work $(compute:THIS_TOOL_INST_ROOT)/libraries/work\n",
        encoding="utf-8",
    )
    fake_importer = _write_fake_cds_text_to_5x(tmp_path / "cdsTextTo5x")
    environment = os.environ.copy()
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "NL2VIEW_TEST_RECORD": str(tmp_path / "record.json"),
            "NL2VIEW_TEST_LIBRARY_PATH": str(tmp_path / "work"),
        }
    )

    completed = subprocess.run(
        [
            str(ENTRY),
            "--cds-text-to-5x",
            str(fake_importer),
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
    assert "installation-root expression requires cdsLibDebug" in completed.stderr
    assert not (tmp_path / "record.json").exists()

def test_cds_lib_keeps_hash_in_library_path(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "oa#libs" / "work"
    library.mkdir(parents=True)
    (tmp_path / "cds.lib").write_text(
        "DEFINE work ./oa#libs/work # project library\n", encoding="utf-8"
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
        extra_environment={"NL2VIEW_TEST_LIBRARY_PATH": str(library)},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert (library / "top" / "spectreText" / "spectre.spectre").is_file()

def test_cds_lib_accepts_double_dash_comment(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(
        "-- project libraries\nDEFINE work ./work -- project library\n",
        encoding="utf-8",
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
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None

def test_cds_lib_ignores_unresolved_soft_entries(tmp_path: Path) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(
        "SOFTINCLUDE $MISSING_OPTIONAL/cds.lib\n"
        "SOFTDEFINE absent $MISSING_OPTIONAL/library\n"
        "DEFINE work ./work\n",
        encoding="utf-8",
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
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None

@pytest.mark.parametrize("statement", ("DEFINE", "SOFTDEFINE"))
def test_cds_lib_invalid_redefinition_preserves_existing_library(
    tmp_path: Path, statement: str
) -> None:
    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    library = tmp_path / "work"
    library.mkdir()
    (tmp_path / "cds.lib").write_text(
        f"DEFINE work ./work\n{statement} work ./missing\n", encoding="utf-8"
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
        extra_environment={"NL2VIEW_TEST_IMPORT_LIBRARY_PATH": str(library)},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
