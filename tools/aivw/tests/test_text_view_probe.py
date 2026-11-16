from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil

from aivworkbench import text_view_probe
from aivworkbench.process import ProcessResult
from aivworkbench.profiles import load_profile, product_root
from aivworkbench.workspace import resolve_launch_paths


def _temporary_profile(tmp_path: Path):
    payload_root = tmp_path / "payload"
    payload_root.mkdir()
    setup = tmp_path / "setup.bashrc"
    setup.write_text(f"export PROJ_AMS_DB_DIR={payload_root}\n", encoding="utf-8")
    return replace(load_profile("amsverify"), setup_script=setup)


def _result(command: tuple[str, ...], cwd: Path, log_file: Path, returncode: int = 0):
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("mock process\n", encoding="utf-8")
    return ProcessResult(
        command=command,
        cwd=str(cwd),
        started_at="2026-08-30T00:00:00+00:00",
        finished_at="2026-08-30T00:00:01+00:00",
        elapsed_seconds=1.0,
        returncode=returncode,
        timed_out=False,
        log_file=str(log_file),
    )


def _install_successful_process_mock(monkeypatch, calls: list[dict[str, object]]) -> None:
    def fake_run_process_group(command, *, cwd, environment, log_file, timeout):
        argv = tuple(str(item) for item in command)
        calls.append(
            {
                "command": argv,
                "cwd": cwd,
                "environment": dict(environment),
                "log_file": log_file,
                "timeout": timeout,
            }
        )
        if argv[0].endswith("cdsTextTo5x"):
            source = Path(argv[-1])
            primary = (
                Path(environment["AIVW_PROBE_LIBRARY_PATH"])
                / environment["AIVW_PROBE_CELL"]
                / environment["AIVW_PROBE_VIEW"]
                / environment["AIVW_PROBE_PRIMARY"]
            )
            primary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, primary)
            tool_log = Path(argv[argv.index("-LOG") + 1])
            tool_log.write_text("mock cdsTextTo5x\n", encoding="utf-8")
        elif argv[0].endswith("dbAccess") and argv[-1].endswith(
            "text_view_probe_readback.il"
        ):
            primary = (
                Path(environment["AIVW_PROBE_LIBRARY_PATH"])
                / environment["AIVW_PROBE_CELL"]
                / environment["AIVW_PROBE_VIEW"]
                / environment["AIVW_PROBE_PRIMARY"]
            )
            Path(environment["AIVW_PROBE_REPORT"]).write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "view_exists": True,
                        "primary_exists": True,
                        "primary_path": str(primary),
                        "shadow_opened": True,
                        "shadow_cell_view_type": "netlist",
                        "terminal_count": 2,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return _result(argv, cwd, log_file)

    monkeypatch.setattr(text_view_probe, "run_process_group", fake_run_process_group)


def test_text_view_probe_enforces_copy_mode_and_create_update_restore(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict[str, object]] = []
    _install_successful_process_mock(monkeypatch, calls)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()

    manifest = text_view_probe.run_systemverilog_text_view_probe(
        _temporary_profile(tmp_path),
        resolve_launch_paths(launch_dir),
        environment={"PATH": "/qualified/bin", "CDS_MPS_SESSION": "inherited"},
        cds_text_to_5x="/qualified/bin/cdsTextTo5x",
        dbaccess="/qualified/bin/dbAccess",
    )

    assert manifest["status"] == "PASS"
    assert manifest["manifest_pair"]["status"] == "PASS"
    assert manifest["manifest_pair"]["run_status"] == "PASS"
    importer_calls = [
        item for item in calls if str(item["command"][0]).endswith("cdsTextTo5x")
    ]
    assert len(importer_calls) == 3
    assert all(item["environment"]["CDS5X_NOLINK"] == "1" for item in importer_calls)
    assert all("CDS_MPS_SESSION" not in item["environment"] for item in calls)
    payload_root = Path(manifest["details"]["payload_root"])
    evidence = json.loads(
        (payload_root / "publish/readback/probe-evidence.json").read_text(encoding="utf-8")
    )
    hashes = evidence["content_hashes"]
    assert hashes["create"] != hashes["update"]
    assert hashes["restore"] == hashes["create"]
    assert evidence["readbacks"]["create"]["shadow_cell_view_type"] == "netlist"
    assert evidence["direct_oa_internal_file_write"] is False


def test_text_view_probe_keeps_manifest_integrity_separate_from_failed_run(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict[str, object]] = []
    _install_successful_process_mock(monkeypatch, calls)
    successful = text_view_probe.run_process_group

    def fail_first_readback(command, *, cwd, environment, log_file, timeout):
        argv = tuple(str(item) for item in command)
        if argv[0].endswith("dbAccess") and argv[-1].endswith(
            "text_view_probe_readback.il"
        ):
            return _result(argv, cwd, log_file, returncode=1)
        return successful(
            command,
            cwd=cwd,
            environment=environment,
            log_file=log_file,
            timeout=timeout,
        )

    monkeypatch.setattr(text_view_probe, "run_process_group", fail_first_readback)
    launch_dir = tmp_path / "launch"
    launch_dir.mkdir()
    manifest = text_view_probe.run_systemverilog_text_view_probe(
        _temporary_profile(tmp_path),
        resolve_launch_paths(launch_dir),
        environment={"PATH": "/qualified/bin"},
        cds_text_to_5x="/qualified/bin/cdsTextTo5x",
        dbaccess="/qualified/bin/dbAccess",
    )

    assert manifest["status"] == "FAIL_TEXT_VIEW_READBACK"
    assert manifest["manifest_pair"]["status"] == "PASS"
    assert manifest["manifest_pair"]["run_status"] == "FAIL_TEXT_VIEW_READBACK"


def test_text_view_readback_lets_oa_infer_the_shadow_database_type() -> None:
    source = (product_root() / "skill" / "text_view_probe_readback.il").read_text(
        encoding="utf-8"
    )
    assert 'dbOpenCellViewByType(libraryName cellName viewName nil "r")' in source
    assert 'dbOpenCellViewByType(libraryName cellName viewName viewType "r")' not in source
