"""nl2view diagnostics behavior cases."""

from __future__ import annotations

from pathlib import Path


from nl2view_fixtures import (
    _run,
)

def test_rejects_cadence_error_diagnostic_with_zero_exit(tmp_path: Path) -> None:
    source = tmp_path / "broken.sp"
    source.write_text(".ENDS without_subckt\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()

    completed, payload = _run(
        tmp_path,
        "--format",
        "spice",
        "--library",
        "work",
        "--cell",
        "broken",
        str(source),
        extra_environment={
            "NL2VIEW_TEST_DIAGNOSTIC": (
                "ERROR (TISPC-2007): Unable to parse the input file."
            )
        },
    )

    assert payload is not None
    assert completed.returncode == 1
    assert "ERROR (TISPC-2007)" in completed.stdout

def test_ignores_missing_optional_aivivc_gdm_after_successful_import(
    tmp_path: Path,
) -> None:
    """An unavailable optional GDM plug-in must not hide a valid OA import."""

    source = tmp_path / "top.scs"
    source.write_text("simulator lang=spectre\n", encoding="utf-8")
    (tmp_path / "cds.lib").write_text("DEFINE work ./work\n", encoding="utf-8")
    (tmp_path / "work").mkdir()
    diagnostic = "\n".join(
        (
            "Warning (gdmiExecGetConfig): Got stderr from aivivcgdmconfig command.",
            " Error (gdmForkExec): Unable to run the aivivcgdmconfig command: No such file or directory",
            'Error (gdmLoadSharedLib): GDM is unable to load the shared library "libgdmaivivc_sh.so".',
            "Error (gdmLoadSharedLib): The message returned from the system is: libgdmaivivc_sh.so: cannot open shared object file: No such file or directory",
            "Error (gdmLoadSharedLib): Contact the owner of the library to resolve the problem",
            "before running the command again.",
            "Error (gdmiLoadDMLibrary): GDM failed to load the DM library './libgdmaivivc_sh.so' for the DM system 'aivivc'",
            "Check if this library is on the CDS_INST_DIR tools/lib and/or tools/lib/64bit,",
            "Error (gdmImportDMSystem): DM system 'aivivc' as specified in file '' is not available.",
        )
    )

    completed, payload = _run(
        tmp_path,
        "--format",
        "spectre",
        "--library",
        "work",
        "--cell",
        "top",
        "--log",
        str(tmp_path / "nl2view.log"),
        str(source),
        extra_environment={"NL2VIEW_TEST_DIAGNOSTIC": diagnostic},
    )

    assert completed.returncode == 0, completed.stderr
    assert payload is not None
    assert (tmp_path / "work" / "top" / "spectreText" / "netlist.oa").is_file()
    evidence = (tmp_path / "nl2view.log").read_text(encoding="utf-8")
    assert "outcome: succeeded" in evidence
    assert "gdmLoadSharedLib" in evidence

def test_still_rejects_unrelated_gdm_error_after_successful_import(
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
                "Error (gdmImportDMSystem): unrelated DM failure"
            )
        },
    )

    assert payload is not None
    assert completed.returncode == 1
