"""Execute one isolated defaults probe and retain failure evidence."""

from __future__ import annotations

from dataclasses import replace
import os
from threading import Event
from typing import Optional, Mapping, Callable
from .artifacts import JobPaths, exclusive_job_lock, atomic_write_text, sha256_file, request_digest as artifact_request_digest
from .defaults_values import DEFAULTS_OUTPUT_ENV, DEFAULTS_PROJECT_ENV, DEFAULTS_RESULTS_ENV
from .defaults_request import DefaultsProbeRequest
from .defaults_result import DefaultsReport
from .defaults_probe import render_defaults_probe_script
from .defaults_report import parse_defaults_report
from .model_request import NetlistRequest
from .environment import write_cds_lib_overlay, isolated_environment
from .process import ProcessResult, run_isolated, environment_digest
from .errors import MtsNetlistorError
from .cadence_executable import _cadence_executable
from .workflow_manifest import _write_json, _timestamp, _manifest_update, _process_start_time


def probe_defaults(
    request: DefaultsProbeRequest | NetlistRequest,
    *,
    environ: Optional[Mapping[str, str]] = None,
    ocean: Optional[str] = None,
    timeout: float = 180.0,
    cancel_event: Optional[Event] = None,
    output_callback: Optional[Callable[[str], object]] = None,
) -> DefaultsReport:
    """Read PDK-initialized ASI defaults in a source-only worker.

    This operation deliberately has no target session and never calls the
    netlister.  It uses the same private cds.lib overlay and MPS-detached
    environment as source generation, so a callback cannot alter the host
    project Virtuoso session.
    """
    if isinstance(request, NetlistRequest):
        value = request.validate()
        probe_request = DefaultsProbeRequest(value.source, value.dialect)
    else:
        probe_request = request.validate()
        value = probe_request.source
    digest = artifact_request_digest(
        {
            "operation": "defaults_probe",
            "cds_lib": str(value.cds_lib),
            "library": value.library,
            "cell": value.cell,
            "view": value.view,
            "dialect": probe_request.dialect,
            "provider": probe_request.provider,
            "mae_setup": None if probe_request.mae_setup is None else probe_request.mae_setup.to_dict(),
            "startup_file": None if value.startup_file is None else str(value.startup_file),
            "simrc": None if value.simrc is None else str(value.simrc),
        }
    )
    namespace = JobPaths.namespace_for(
        value.library, value.cell, value.view, environ=environ
    )
    # Probe artifacts have their own namespace so a defaults read cannot race
    # or overwrite a normal netlisting run for the same source design.
    with exclusive_job_lock(namespace / ".mts-netlistor-probe.lock"):
        paths = JobPaths.create(
            value.library,
            value.cell,
            value.view,
            digest,
            environ=environ,
            namespace=namespace,
        )
        report_path = paths.source / "defaults.json"
        script_path = paths.source / "defaults.ocn"
        ocean_log = paths.source / "defaults.log"
        manifest_path = paths.run / "defaults-manifest.json"
        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "pdk_defaults_probe",
            "status": "created",
            "request_digest": digest,
            "started_at": _timestamp(),
            "owner": {
                "pid": os.getpid(),
                "start_time": _process_start_time(os.getpid()),
            },
            "source": {
                "cds_lib": str(value.cds_lib),
                "library": value.library,
                "cell": value.cell,
                "view": value.view,
            },
            "dialect": probe_request.dialect,
            # Keep the identity in the manifest as well as its digest.  The
            # worker report is validated against exactly these fields below,
            # but human-readable evidence is essential when diagnosing a PDK
            # or Maestro setup mismatch after the short-lived worker exits.
            "provider": probe_request.provider,
            "mae_setup": (
                None
                if probe_request.mae_setup is None
                else probe_request.mae_setup.to_dict()
            ),
            "processes": [],
            "report": str(report_path),
        }
        _write_json(manifest_path, manifest)
        result: ProcessResult | None = None
        try:
            atomic_write_text(
                script_path,
                render_defaults_probe_script(
                    value,
                    probe_request.dialect,
                    project_dir=paths.source,
                    result_dir=paths.source,
                    output_path=report_path,
                    provider=probe_request.provider,
                    mae_setup=probe_request.mae_setup,
                ),
            )
            overlay = write_cds_lib_overlay(
                value.cds_lib, paths.source / "source-overlay.cds.lib"
            )
            child_environment = isolated_environment(
                environ, cds_lib=overlay, workdir=paths.source
            )
            child_environment[DEFAULTS_OUTPUT_ENV] = str(report_path)
            child_environment[DEFAULTS_PROJECT_ENV] = str(paths.source)
            child_environment[DEFAULTS_RESULTS_ENV] = str(paths.source)
            executable = _cadence_executable(
                ocean, "ocean", environ=child_environment
            )
            command = [
                executable,
                "-nograph",
                "-nocdsinit",
                "-cdslib",
                str(overlay),
                "-replay",
                str(script_path),
                "-log",
                str(ocean_log),
            ]
            _manifest_update(
                manifest_path,
                manifest,
                status="probing_runtime_defaults",
                source_command=command,
                source_overlay=str(overlay),
                source_overlay_sha256=sha256_file(overlay),
                source_environment_digest=environment_digest(child_environment),
            )
            result = run_isolated(
                command,
                cwd=paths.source,
                environment=child_environment,
                timeout=timeout,
                cancel=cancel_event,
                log_file=paths.logs / "defaults-worker.log",
                output_callback=output_callback,
                monitor_file=ocean_log,
            )
            # ``ocean -log`` writes the Cadence transcript separately from
            # stdout/stderr.  Merge it into the durable worker log after the
            # process exits so callback diagnostics and ASI warnings remain
            # available beside the process metadata.
            if ocean_log.is_file():
                try:
                    transcript = ocean_log.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    worker_log = paths.logs / "defaults-worker.log"
                    worker_log.write_text(
                        getattr(result, "stdout", "")
                        + getattr(result, "stderr", "")
                        + "\n"
                        + transcript,
                        encoding="utf-8",
                    )
                except OSError:
                    pass
            manifest["processes"] = [{
                "domain": "source_defaults",
                "pid": getattr(result, "pid", 0),
                "ppid": getattr(result, "ppid", 0),
                "pgid": getattr(result, "pgid", 0),
                "argv": list(getattr(result, "argv", command)),
                "cwd": getattr(result, "cwd", str(paths.source)),
                "environment_digest": getattr(result, "environment_digest", ""),
                "started_at": getattr(result, "started_at", ""),
                "finished_at": getattr(result, "finished_at", ""),
                "returncode": getattr(result, "returncode", 1),
                "timed_out": getattr(result, "timed_out", False),
                "canceled": getattr(result, "canceled", False),
                "lifetime": getattr(result, "lifetime", "task"),
                "task_id": getattr(result, "task_id", ""),
                "worker_started_at": getattr(result, "worker_started_at", ""),
                "runtime_pid": getattr(result, "runtime_pid", 0),
            }]
            if getattr(result, "canceled", False):
                raise MtsNetlistorError("defaults probe canceled")
            if getattr(result, "timed_out", False):
                raise MtsNetlistorError(
                    f"defaults probe timed out after {timeout:g}s"
                )
            if getattr(result, "returncode", 1) != 0:
                detail = (
                    getattr(result, "stderr", "") or getattr(result, "stdout", "")
                ).strip().splitlines()
                raise MtsNetlistorError(
                    "source defaults worker failed"
                    + (f": {detail[-1]}" if detail else "")
                )
            report = parse_defaults_report(
                report_path,
                source=value,
                dialect=probe_request.dialect,
                provider=probe_request.provider,
                mae_setup=probe_request.mae_setup,
                base_dirs=(paths.source, value.cds_lib.parent),
            )
            if report.status != "succeeded":
                raise MtsNetlistorError("source defaults report did not succeed")
            final_snapshot = report.after_startup_simrc
            if not any(
                final_snapshot.get(name)
                for name in (
                    "model_files",
                    "environment_options",
                    "simulator_options",
                )
            ):
                final_snapshot = report.after_design
            _manifest_update(
                manifest_path,
                manifest,
                status="succeeded",
                finished_at=_timestamp(),
                report_sha256=sha256_file(report_path),
                model_count=len(final_snapshot.get("model_files", ())),
                simulator_option_count=len(
                    final_snapshot.get("simulator_options", {})
                ),
            )
            return replace(
                report,
                run_dir=paths.run,
                report_path=report_path,
                process=result,
            )
        except Exception as exc:
            canceled = (
                (result is not None and getattr(result, "canceled", False))
                or (
                    cancel_event is not None
                    and getattr(cancel_event, "is_set", lambda: False)()
                )
            )
            failure_kind = "canceled" if canceled else "failed"
            if result is not None and getattr(result, "timed_out", False):
                failure_kind = "timeout"
            _manifest_update(
                manifest_path,
                manifest,
                status="canceled" if canceled else "failed",
                finished_at=_timestamp(),
                failure={
                    "kind": failure_kind,
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            raise
