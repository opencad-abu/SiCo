"""Target-only Spectre stopping view and model-binding CDF publisher."""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from .artifacts import atomic_write_text, sha256_file
from .corner_library import rename_library, validate_library
from .environment import isolated_environment, write_cds_lib_overlay
from .errors import MtsNetlistorError, RequestValidationError
from .process import run_isolated
from .symbol import _skill_string
from cadview.nl2view import find_cds_lib_debug


def _render_binding_script(library: str, cell: str, ports: tuple[str, ...], overwrite: bool, report: Path) -> str:
    from cadcontext import worker_call
    names = tuple(re.sub(r"\\(.)", r"\1", p) for p in ports)
    return worker_call("mtsRuntimeBinding", library, cell, names, overwrite, report) + "exit()\n"


def publish_binding(request, session, *, netlist, run_dir, ocean="ocean", timeout=120.0, cancel_event=None, strict_ownership=False):
    from cadview.cdslib import resolve_library_path
    from .publish import PublicationResult, preflight_publication, validate_run_artifact

    value = request.validate()
    preflight = preflight_publication(value, session, netlist=netlist, include_symbol=False, include_text=True)
    source = Path(netlist).resolve()
    root = Path(run_dir).resolve()
    validate_run_artifact(value, source, root, strict_ownership=strict_ownership)
    original = source.read_text(encoding="utf-8")
    validate_library(original, value.corner_export, value.source.cell)
    cell = preflight.target_cell
    text = rename_library(original, value.corner_export, value.source.cell, cell)
    ports = validate_library(text, value.corner_export, cell)
    staging = root / "publish"
    staging.mkdir(parents=True, exist_ok=True)
    overlay = write_cds_lib_overlay(session.target_cds_lib, staging / "target-overlay.cds.lib", forbidden_paths=(value.source.cds_lib,))
    environment = isolated_environment(None, cds_lib=overlay, workdir=staging, forbidden_cds_lib=value.source.cds_lib)
    executable = shutil.which(ocean, path=environment.get("PATH"))
    if not executable:
        raise RequestValidationError(f"cannot execute target OCEAN: {ocean}")
    resolved = resolve_library_path(overlay, preflight.target_library, environ=environment,
                                    cadence_executable=find_cds_lib_debug(executable, None, allow_path_lookup=True))
    if resolved.resolve() != preflight.target_library_path:
        raise RequestValidationError("target library resolution differs from current session")
    script = staging / "model-binding.il"
    report = staging / "model-binding.report"
    report.unlink(missing_ok=True)
    atomic_write_text(script, _render_binding_script(preflight.target_library, cell, ports, value.target.overwrite_netlist_view, report))
    log = staging / "model-binding.log"
    result = run_isolated([executable, "-nograph", "-nocdsinit", "-cdslib", str(overlay), "-replay", str(script)], cwd=staging,
                          environment=environment, timeout=timeout, cancel=cancel_event, log_file=log)
    if result.returncode or result.timed_out or result.canceled or not report.is_file() or not report.read_text().startswith("binding=succeeded\n"):
        raise MtsNetlistorError(f"model binding failed; inspect {log}")
    view = preflight.text_view_path
    if not view.is_dir():
        raise MtsNetlistorError("model binding worker did not create the stopping view")
    if cancel_event is not None and cancel_event.is_set():
        raise MtsNetlistorError("model binding canceled before library publication")
    model_file = view / f"{cell}_corners.scs"
    atomic_write_text(model_file, text, refuse_existing=not value.target.overwrite_netlist_view)
    message = (f'Model File: {model_file}; Section: VAR("{value.corner_export.variable}"); '
               f'values: {", ".join(chr(34)+p.name+chr(34) for p in value.corner_export.profiles)}; '
               f'temperature: {value.temperature_mode}; sha256={sha256_file(model_file)}')
    return PublicationResult("succeeded", preflight.target_library, cell, "spectre", model_file, log,
                             not preflight.text_existed_before, preflight.text_existed_before, overlay, message)
