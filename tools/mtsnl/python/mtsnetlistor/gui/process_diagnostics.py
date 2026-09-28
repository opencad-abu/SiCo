"""Render safe catalog timings and actionable publication evidence."""

from __future__ import annotations

from pathlib import Path
from ..project import ProjectContext


class ProcessDiagnostics:
    def __init__(self, append, show_status) -> None:
        self._append = append
        self._show_status = show_status

    def source_catalog(self, result) -> None:
        """Render cache/provider timings without dumping sensitive environments."""

        diagnostics = tuple(getattr(result, "diagnostics", ()))
        interesting = tuple(
            item
            for item in diagnostics
            if item.startswith((
                "source_catalog_cache=",
                "source_fingerprint_ms=",
                "source_provider_ms=",
                "source_total_ms=",
                "source_catalog_cache_age_ms=",
            ))
        )
        if interesting:
            self._append("Source catalog: " + "; ".join(interesting))

    def project(self, context: ProjectContext) -> None:
        """Render module/project durations without exposing environment data."""

        values = dict(getattr(context, "timings", ()))
        ordered = tuple(
            f"{name}={max(0.0, float(values[name])):.3f}"
            for name in (
                "modulecmd_purge_ms",
                "modulecmd_load_ms",
                "project_resolve_ms",
            )
            if name in values
        )
        if ordered:
            self._append("Project timing: " + "; ".join(ordered))

    def publication(self, publication) -> None:
        """Show the complete publication evidence without hiding the status.

        ``manual_cleanup_required`` means that a target OA mutation may have
        happened before a worker reported failure.  The GUI must therefore
        tell the operator exactly what to inspect and where the worker logs
        live; it must not suggest that deleting a filesystem directory is a
        sufficient cleanup operation.
        """

        status = str(getattr(publication, "status", "unknown"))
        self._append(f"Publication: {status}")
        message = str(getattr(publication, "message", "")).strip()
        if message:
            self._append(f"Publication message: {message}")
        for path in getattr(publication, "affected_views", ()):
            self._append(f"Affected target view: {path}")
        for path in getattr(publication, "log_files", ()):
            state = "present" if Path(path).is_file() else "missing"
            self._append(f"Publication evidence ({state}): {path}")
        for detail in getattr(publication, "diagnostics", ()):
            self._append(f"Publication diagnostic: {detail}")
        for instruction in getattr(publication, "cleanup_instructions", ()):
            self._append(f"ACTION: {instruction}")
        if status == "manual_cleanup_required":
            self._show_status(
                "Publication requires manual OA inspection; see the log for affected views and evidence paths"
            )
        elif message:
            self._show_status(message)
