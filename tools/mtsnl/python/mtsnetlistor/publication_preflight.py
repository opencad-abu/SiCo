"""Reject target identity conflicts before any OA mutation."""

from __future__ import annotations

from pathlib import Path
import shutil
from .environment import SessionDescriptor
from .errors import RequestValidationError, IsolationError
from .model_request import NetlistRequest
from .publication_result import PublicationPreflight
try:
    from cadview.nl2view import find_executable
except ImportError:  # pragma: no cover
    find_executable = None


def _target_path(session: SessionDescriptor, library: str) -> Path:
    expected = session.target_library_paths.get(library)
    if expected is None:
        raise RequestValidationError(f"target library is not part of the current session: {library}")
    path = Path(expected).expanduser().resolve()
    if not path.is_dir() or not path.exists():
        raise RequestValidationError(f"target library path is unavailable: {path}")
    if not path.stat().st_mode & 0o222:
        raise RequestValidationError(f"target library is read-only: {path}")
    return path


def preflight_publication(
    request: NetlistRequest,
    session: SessionDescriptor,
    *,
    netlist: str | Path | None = None,
    cds_text_to_5x: str = "cdsTextTo5x",
    include_symbol: bool | None = None,
    include_text: bool | None = None,
) -> PublicationPreflight:
    """Resolve all target destinations and reject conflicts before mutation."""

    value = request.validate()
    validated = session.validate()
    library = value.target.library
    cell = value.target.cell or value.source.cell
    if not library:
        raise RequestValidationError("target library is required for publication")
    want_symbol = value.target.generate_symbol_view if include_symbol is None else bool(include_symbol)
    want_text = value.target.generate_netlist_view if include_text is None else bool(include_text)
    if not want_symbol and not want_text:
        raise RequestValidationError("no publication view is enabled")
    library_path = _target_path(validated, library)
    symbol_path = library_path / cell / "symbol" if want_symbol else None
    text_path = library_path / cell / value.text_view if want_text else None
    if value.dialect == "spectre" and want_text:
        conflicting_views = ("spectreText",) if value.corner_export.mode == "library" else ("spectre",)
        for conflicting in conflicting_views:
            if (library_path / cell / conflicting).exists():
                raise RequestValidationError(f"target has conflicting {conflicting} view; choose a separate target cell")
        if value.corner_export.mode == "library" and netlist is not None:
            from .corner_library import validate_library
            validate_library(Path(netlist).read_text(encoding="utf-8"), value.corner_export, value.source.cell)
    symbol_existed = symbol_path is not None and symbol_path.exists()
    text_existed = text_path is not None and text_path.exists()
    conflicts = []
    if symbol_existed and not value.target.overwrite_symbol_view:
        conflicts.append(symbol_path)
    if text_existed and not value.target.overwrite_netlist_view:
        conflicts.append(text_path)
    if conflicts:
        rendered = ", ".join(str(path) for path in conflicts)
        raise RequestValidationError(
            f"target publication destination already exists (overwrite policy is reject): {rendered}"
        )
    source_path = None
    if want_text:
        if netlist is None:
            raise RequestValidationError("MTS netlist is required for text publication")
        source_path = Path(netlist).expanduser().resolve()
        if not source_path.is_file() or source_path.stat().st_size == 0:
            raise RequestValidationError(f"MTS netlist is missing or empty: {source_path}")
    executable = None
    if want_text and value.corner_export.mode != "library":
        executable = find_executable(cds_text_to_5x) if find_executable else shutil.which(cds_text_to_5x)
        if not executable:
            raise RequestValidationError(f"cannot execute cdsTextTo5x: {cds_text_to_5x}")
    if want_symbol and value.source.library == library and value.source.cell == cell:
        raise IsolationError("source and target symbol cell are identical; refusing self-publication")
    return PublicationPreflight(
        library,
        cell,
        library_path,
        symbol_path,
        text_path,
        source_path,
        executable,
        symbol_existed,
        text_existed,
    )
