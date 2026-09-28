"""Execute and scope one source request before publishing its stable netlist."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
from threading import Event
from typing import Optional, Mapping, Callable
from .artifacts import JobPaths, exclusive_job_lock, atomic_write_text, sha256_file
from .request_data import canonical_request_digest, canonical_request_json, request_to_dict
from .model_request import NetlistRequest
from .model_entries import CornerExport
from .environment import write_cds_lib_overlay, isolated_environment
from .ocean import render_ocean_script, raw_path_from_output
from .process import ProcessResult, run_isolated, environment_digest
from .scoper import scope_netlist
from .corner_library import assemble_library, inherit_temperature
from .generation_result import GenerationResult
from .errors import MtsNetlistorError, RequestValidationError
from .cadence_executable import _cadence_executable
from .workflow_manifest import _write_json, _timestamp, _manifest_update, _process_start_time


def _check_raw_dialect(path: Path, dialect: str) -> None:
    expected = "input.scs" if dialect == "spectre" else "input.ckt"
    if path.name != expected:
        raise RequestValidationError(f"unexpected {dialect} raw filename: {path.name}")


def generate(
    request: NetlistRequest,
    *,
    environ: Optional[Mapping[str, str]] = None,
    virtuoso: Optional[str] = None,
    ocean: Optional[str] = None,
    timeout: float = 600.0,
    cancel_event: Optional[Event] = None,
    output_callback: Optional[Callable[[str], object]] = None,
    allow_non_authoritative: bool = False,
) -> GenerationResult:
    """Run one source generation and publish only the stable MTS file.

    This function never opens source OA in the caller. The source-only worker
    completes its task and closes the design session before the scoped file
    is publishable. A GUI project can retain that worker for later tasks;
    standalone calls terminate the worker's process group.
    """

    value = request.validate()
    digest = canonical_request_digest(value)
    namespace = JobPaths.namespace_for(
        value.source.library,
        value.source.cell,
        value.source.view,
        environ=environ,
    )
    # Lock the stable design namespace before allocating a run directory.  A
    # timestamp/digest name alone is not a synchronization primitive.
    with exclusive_job_lock(namespace / ".mts-netlistor.lock"):
        paths = JobPaths.create(
            value.source.library,
            value.source.cell,
            value.source.view,
            digest,
            environ=environ,
            namespace=namespace,
        )
        manifest_path = paths.run / "manifest.json"
        manifest: dict[str, object] = {
            "schema_version": 1,
            "status": "created",
            "request_digest": digest,
            "started_at": _timestamp(),
            "owner": {
                "pid": os.getpid(),
                "start_time": _process_start_time(os.getpid()),
            },
            "source": {
                "cds_lib": str(value.source.cds_lib),
                "library": value.source.library,
                "cell": value.source.cell,
                "view": value.source.view,
            },
            "dialect": value.dialect,
            "processes": [],
        }
        _write_json(paths.run / "request.toml", canonical_request_json(value))
        _write_json(paths.run / "resolved-request.json", request_to_dict(value))
        _write_json(manifest_path, manifest)
        result: Optional[ProcessResult] = None
        raw: Optional[Path] = None
        scoped_path: Optional[Path] = None
        report_path: Optional[Path] = None
        stable: Optional[Path] = None
        try:
            root_paths = paths
            profiles = value.corner_export.profiles if value.corner_export.mode == "library" else (None,)
            blocks = []
            for profile in profiles:
                if cancel_event is not None and cancel_event.is_set():
                    raise MtsNetlistorError("corner generation canceled")
                # The per-corner worker emits an ordinary scoped block first;
                # the library-level temperature policy is applied immediately
                # after scoping below. Keep this internal request in fixed
                # mode so validation does not reject its temporary fixed
                # corner_export placeholder.
                corner_request = replace(value, cell_specs=(), corner_export=CornerExport(),
                                         temperature_mode="fixed",
                                         models=profile.models if profile else value.models)
                if profile is not None:
                    paths = replace(root_paths, source=root_paths.source / profile.name,
                                    raw=root_paths.raw / profile.name, scoped=root_paths.scoped / profile.name,
                                    logs=root_paths.logs / profile.name)
                    for directory in (paths.source, paths.raw, paths.scoped, paths.logs):
                        directory.mkdir(parents=True, exist_ok=True)
                    if output_callback:
                        output_callback(f"Generating corner {profile.name}")
                _manifest_update(manifest_path, manifest, status="preparing_source")
                script = render_ocean_script(corner_request, workdir=paths.source, result_dir=paths.source)
                script_path = paths.source / "ocean.ocn"
                atomic_write_text(script_path, script.text)
                source_overlay = write_cds_lib_overlay(
                    value.source.cds_lib,
                    paths.source / "source-overlay.cds.lib",
                )
                environment = isolated_environment(
                    environ,
                    cds_lib=source_overlay,
                    workdir=paths.source,
                )
                _manifest_update(
                    manifest_path,
                    manifest,
                    source_environment_digest=environment_digest(environment),
                    source_overlay=str(source_overlay),
                    source_overlay_sha256=sha256_file(source_overlay),
                )
                # OCEAN is the supported batch front-end for an OCEAN script.
                # ``virtuoso -replay`` replays a Virtuoso interaction log and is
                # not equivalent to loading an OCEAN program.  Keep the legacy
                # ``virtuoso=`` injection argument for fake-worker tests and
                # callers that already use it, but production defaults to ocean.
                executable = _cadence_executable(
                    ocean or virtuoso, "ocean", environ=environment
                )
                ocean_log = paths.source / "ocean.log"
                command = [
                    executable,
                    "-nograph",
                    "-nocdsinit",
                    "-cdslib",
                    str(source_overlay),
                    "-replay",
                    str(script_path),
                    "-log",
                    str(ocean_log),
                ]
                _manifest_update(manifest_path, manifest, status="generating_raw_netlist", source_command=list(command))
                # ``-replay`` is the documented Virtuoso non-graphical input path.
                result = run_isolated(
                    command,
                    cwd=paths.source,
                    environment=environment,
                    timeout=timeout,
                    cancel=cancel_event,
                    log_file=paths.logs / "source-worker.log",
                    output_callback=output_callback,
                    monitor_file=ocean_log,
                )
                process_payload = {
                    "domain": "source",
                    "corner": None if profile is None else profile.name,
                    "pid": result.pid,
                    "ppid": result.ppid,
                    "pgid": result.pgid,
                    "argv": list(result.argv),
                    "cwd": result.cwd,
                    "environment_digest": result.environment_digest,
                    "started_at": result.started_at,
                    "finished_at": result.finished_at,
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "canceled": result.canceled,
                    "lifetime": result.lifetime,
                    "task_id": result.task_id,
                    "worker_started_at": result.worker_started_at,
                    "runtime_pid": result.runtime_pid,
                }
                manifest["processes"].append(process_payload)
                if result.canceled:
                    _manifest_update(manifest_path, manifest, status="canceled", finished_at=_timestamp(), failure={"message": "source generation canceled"})
                    raise MtsNetlistorError("source generation canceled")
                if result.timed_out:
                    message = f"source generation timed out after {timeout:g}s"
                    _manifest_update(manifest_path, manifest, status="failed", finished_at=_timestamp(), failure={"message": message, "kind": "timeout"})
                    raise MtsNetlistorError(message)
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout).strip().splitlines()
                    message = "source Virtuoso failed" + (f": {detail[-1]}" if detail else "")
                    _manifest_update(manifest_path, manifest, status="failed", finished_at=_timestamp(), failure={"message": message, "kind": "worker_exit"})
                    raise MtsNetlistorError(message)
                worker_output = result.stdout + "\n" + result.stderr
                if ocean_log.is_file():
                    # OCEAN's ``-log`` mode writes the replay transcript (and the
                    # raw sentinel) to this file rather than stdout.  Include it
                    # in the durable worker log and use it for artifact discovery.
                    transcript = ocean_log.read_text(encoding="utf-8", errors="replace")
                    worker_output += "\n" + transcript
                    try:
                        worker_log = paths.logs / "source-worker.log"
                        worker_log.write_text(worker_output, encoding="utf-8")
                    except OSError:
                        pass
                raw = raw_path_from_output(worker_output, run_root=paths.run)
                _check_raw_dialect(raw, value.dialect)
                staged_raw = paths.raw / script.expected_filename
                if raw != staged_raw:
                    shutil.copyfile(raw, staged_raw)
                raw = staged_raw
                _manifest_update(manifest_path, manifest, status="parsing_and_scoping", raw_netlist=str(raw), raw_sha256=sha256_file(raw))
                scoped = scope_netlist(raw.read_text(encoding="utf-8", errors="replace"), value.dialect, value.source.cell)
                if value.temperature_mode == "inherit":
                    scoped = inherit_temperature(scoped)
                blocks.append(scoped)
                if profile is not None:
                    atomic_write_text(paths.scoped / f"{value.source.cell}.spe", scoped.output)
                    _write_json(paths.scoped / "parser-report.json", scoped.report())
            paths = root_paths
            output = assemble_library(value.corner_export, value.source.cell, blocks) if value.corner_export.mode == "library" else blocks[0].output
            if cancel_event is not None and cancel_event.is_set():
                raise MtsNetlistorError("generation canceled before stable publication")
            scoped_path = paths.scoped / f"{value.source.cell}{value.output_suffix}"
            atomic_write_text(scoped_path, output)
            report_path = paths.scoped / "parser-report.json"
            _write_json(report_path, ({"schema_version": 1, "mode": "library", "top": value.source.cell,
                "ports": list(blocks[0].ports), "variable": value.corner_export.variable,
                "temperature_mode": value.temperature_mode,
                "corners": [{"name": profile.name, **block.report()} for profile, block in zip(profiles, blocks)]}
                if value.corner_export.mode == "library" else scoped.report()))
            _manifest_update(manifest_path, manifest, status="publishing_stable_netlist", scoped_netlist=str(scoped_path), scoped_sha256=sha256_file(scoped_path))
            stable = paths.stable_output(value.source.cell, value.output_suffix)
            atomic_write_text(stable, output)
            _manifest_update(
                manifest_path,
                manifest,
                status="succeeded",
                finished_at=_timestamp(),
                stable_output=str(stable),
                stable_sha256=sha256_file(stable),
            )
            _write_json(paths.latest, {"request_digest": digest, "status": "succeeded", "run_dir": str(paths.run), "stable_output": str(stable), "finished_at": manifest["finished_at"]})
            assert raw is not None and scoped_path is not None and report_path is not None and stable is not None and result is not None
            return GenerationResult(digest, "succeeded", paths.run, raw, scoped_path, report_path, stable, result,
                                    tuple(profile.name for profile in value.corner_export.profiles))
        except Exception as exc:
            # The explicit terminal update above is retained for cancellation;
            # all other exceptions become failed runs while preserving the
            # original exception and any prior successful stable output.
            current = str(manifest.get("status", ""))
            if current not in {"succeeded", "canceled", "failed"}:
                _manifest_update(
                    manifest_path,
                    manifest,
                    status="canceled" if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)() else "failed",
                    finished_at=_timestamp(),
                    failure={"type": type(exc).__name__, "message": str(exc)},
                )
            raise
