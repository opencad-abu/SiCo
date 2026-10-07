"""Disposable IC23.1 SystemVerilog text-view publication qualification."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .manifest import publish_split_manifests, verify_split_manifests
from .process import run_process_group
from .profiles import Profile, product_root
from .toolchain import parse_setup_exports
from .workspace import (
    LaunchPaths,
    allocate_split_run,
    resolve_project_db_root,
    sha256_file,
    stable_digest,
    write_json_once,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_systemverilog_text_view_probe(
    profile: Profile,
    launch: LaunchPaths,
    *,
    environment: Mapping[str, str],
    cds_text_to_5x: str,
    dbaccess: str,
    timeout: float = 300.0,
) -> dict[str, object]:
    """Create, update, read back, and restore a disposable SV text view."""
    if profile.storage is None:
        raise ValueError(f"profile {profile.name!r} has no split-storage contract")
    if timeout <= 0:
        raise ValueError("text-view probe timeout must be positive")
    exports = parse_setup_exports(profile.setup_script, (profile.storage.payload_env,))
    project_db_root = resolve_project_db_root(
        exports[profile.storage.payload_env],
        launch,
        forbidden_roots=(product_root(),),
    )
    request = {
        "schema_version": 1,
        "kind": "systemverilog-text-view-probe",
        "profile": profile.name,
        "language": "systemverilog",
        "view": "systemVerilog",
        "view_type": "systemVerilogText",
        "primary_file": "verilog.sv",
        "importer": str(Path(cds_text_to_5x).resolve()),
        "dbaccess": str(Path(dbaccess).resolve()),
    }
    request_digest = stable_digest(request)
    run = allocate_split_run(
        launch,
        project_db_root,
        library="AIVW_SCRATCH",
        cell="text_view_probe",
        view="systemVerilog",
        kind="text-view-probe",
        request_digest=request_digest,
    )
    request_path = run.control_root / "request.json"
    write_json_once(request_path, request)
    publish_root = run.payload_root / "publish"
    source_root = publish_root / "sources"
    log_root = publish_root / "logs"
    report_root = publish_root / "readback"
    library_root = publish_root / "oa-library"
    for path in (source_root, log_root, report_root, library_root):
        path.mkdir(parents=True)
    suffix = re.sub(r"[^A-Za-z0-9_]", "_", run.run_id[-28:])
    library = "AIVW_SCRATCH_" + suffix
    cell = "aivw_sv_probe"
    view = "systemVerilog"
    view_type = "systemVerilogText"
    primary = "verilog.sv"
    cds_lib = publish_root / "cds.lib"
    cds_lib.write_text(f"DEFINE {library} {library_root}\n", encoding="utf-8")
    source_v1 = source_root / "candidate-v1.sv"
    source_v2 = source_root / "candidate-v2.sv"
    source_v1.write_text(_source(cell, invert=False), encoding="utf-8")
    source_v2.write_text(_source(cell, invert=True), encoding="utf-8")
    run_environment = dict(environment)
    for name in tuple(run_environment):
        if name.startswith("CDS_MPS_"):
            run_environment.pop(name, None)
    run_environment.update(
        {
            "CDS_LIB": str(cds_lib),
            "CDS_CDSLIB": str(cds_lib),
            "CDS5X_NOLINK": "1",
            "AIVW_PROBE_LIBRARY": library,
            "AIVW_PROBE_LIBRARY_PATH": str(library_root),
            "AIVW_PROBE_CELL": cell,
            "AIVW_PROBE_VIEW": view,
            "AIVW_PROBE_VIEW_TYPE": view_type,
            "AIVW_PROBE_PRIMARY": primary,
        }
    )
    init_log = log_root / "library-init.log"
    init_result = run_process_group(
        (
            dbaccess,
            "-cdslib",
            str(cds_lib),
            "-load",
            str(product_root() / "skill" / "text_view_probe_init.il"),
        ),
        cwd=publish_root,
        environment=run_environment,
        log_file=init_log,
        timeout=min(timeout, 120.0),
    )
    stages: dict[str, Any] = {"library_init": init_result.to_dict()}
    readbacks: dict[str, Any] = {}
    payload_artifacts: list[Path] = [cds_lib, source_v1, source_v2, init_log]
    status = "PASS"
    error = ""
    if init_result.returncode != 0 or init_result.timed_out:
        status = "BLOCKED_ENVIRONMENT"
        error = "dbAccess could not create the disposable probe library"
    hashes: dict[str, str] = {}
    if status == "PASS":
        for stage, source in (("create", source_v1), ("update", source_v2), ("restore", source_v1)):
            import_log = log_root / f"{stage}-import.log"
            command = (
                cds_text_to_5x,
                "-CDSLIB",
                str(cds_lib),
                "-LANG",
                "systemverilog",
                "-LIB",
                library,
                "-CELL",
                cell,
                "-VIEW",
                view,
                "-SHADOW",
                "-LOG",
                str(log_root / f"{stage}-cdsTextTo5x.log"),
                str(source),
            )
            execution = run_process_group(
                command,
                cwd=publish_root,
                environment=run_environment,
                log_file=import_log,
                timeout=timeout,
            )
            stages[stage] = execution.to_dict()
            payload_artifacts.append(import_log)
            tool_log = log_root / f"{stage}-cdsTextTo5x.log"
            if tool_log.is_file():
                payload_artifacts.append(tool_log)
            if execution.returncode != 0 or execution.timed_out:
                status = "FAIL_TEXT_VIEW_IMPORT"
                error = f"cdsTextTo5x failed during {stage}"
                break
            report = report_root / f"{stage}.json"
            readback_log = log_root / f"{stage}-readback.log"
            readback_environment = dict(run_environment)
            readback_environment["AIVW_PROBE_REPORT"] = str(report)
            inspection = run_process_group(
                (
                    dbaccess,
                    "-cdslib",
                    str(cds_lib),
                    "-load",
                    str(product_root() / "skill" / "text_view_probe_readback.il"),
                ),
                cwd=publish_root,
                environment=readback_environment,
                log_file=readback_log,
                timeout=min(timeout, 120.0),
            )
            stages[f"{stage}_readback"] = inspection.to_dict()
            payload_artifacts.append(readback_log)
            if inspection.returncode != 0 or inspection.timed_out or not report.is_file():
                status = "FAIL_TEXT_VIEW_READBACK"
                error = f"DD/OA readback failed during {stage}"
                break
            readback = json.loads(report.read_text(encoding="utf-8"))
            readbacks[stage] = readback
            payload_artifacts.append(report)
            primary_path = Path(str(readback.get("primary_path", ""))).resolve()
            if (
                readback.get("view_exists") is not True
                or readback.get("primary_exists") is not True
                or readback.get("shadow_opened") is not True
                or not primary_path.is_file()
                or not primary_path.is_relative_to(library_root.resolve())
            ):
                status = "FAIL_TEXT_VIEW_READBACK"
                error = f"text view, primary file, or shadow database is incomplete during {stage}"
                break
            hashes[stage] = sha256_file(primary_path)
            if hashes[stage] != sha256_file(source):
                status = "FAIL_TEXT_VIEW_READBACK"
                error = f"primary content hash does not match imported candidate during {stage}"
                break
            if stage == "update" and hashes.get("create") == hashes["update"]:
                status = "FAIL_TEXT_VIEW_UPDATE"
                error = "update did not change the primary content hash"
                break
            if stage == "restore" and hashes.get("create") != hashes["restore"]:
                status = "FAIL_TEXT_VIEW_RESTORE"
                error = "restore did not reproduce the original primary content hash"
                break
    finished_at = _now()
    evidence_path = report_root / "probe-evidence.json"
    write_json_once(
        evidence_path,
        {
            "schema_version": 1,
            "status": status,
            "error": error,
            "target": {
                "library": library,
                "library_path": str(library_root),
                "cell": cell,
                "view": view,
                "view_type": view_type,
                "primary_file": primary,
            },
            "stages": stages,
            "readbacks": readbacks,
            "content_hashes": hashes,
            "expected_old_hash_enforced_by_probe": hashes.get("create", ""),
            "source_oa_touched": False,
            "disposable_library_only": True,
            "direct_oa_internal_file_write": False,
        },
    )
    payload_artifacts.append(evidence_path)
    report_path = run.control_root / "report.json"
    write_json_once(
        report_path,
        {
            "schema_version": 1,
            "status": status,
            "error": error,
            "probe": "systemverilog-text-view-publication",
            "disposable_target": f"{library}/{cell}/{view}",
            "content_hashes": hashes,
        },
    )
    control, _payload = publish_split_manifests(
        run,
        request_digest=request_digest,
        status=status,
        control_details={
            "producer_kind": "systemverilog-text-view-probe",
            "started_at": stages["library_init"]["started_at"],
            "finished_at": finished_at,
            "error": error,
            "target": {"library": library, "cell": cell, "view": view},
            "payload_root": str(run.payload_root),
            "evidence": "publish/readback/probe-evidence.json",
        },
        payload_details={
            "producer_kind": "systemverilog-text-view-probe",
            "disposable_oa_library": str(library_root),
            "artifact_policy": "logs, sources, readbacks indexed; OA internals retained but unindexed",
        },
        control_artifacts=(request_path, report_path),
        payload_artifacts=payload_artifacts,
    )
    result = dict(control)
    result["manifest_pair"] = verify_split_manifests(
        run.control_manifest, run.payload_manifest
    )
    return result


def _source(module: str, *, invert: bool) -> str:
    expression = "~a" if invert else "a"
    return f"""module {module}(input logic a, output logic y);\n  assign y = {expression};\nendmodule\n"""


__all__ = ["run_systemverilog_text_view_probe"]
