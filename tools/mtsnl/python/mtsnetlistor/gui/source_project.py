"""Select a detached source project and guard the host target cds.lib boundary."""

from __future__ import annotations
import inspect
from pathlib import Path
from cadgui.prompts import ask_file, notice
from ..project import resolve_project


def _accepts_keyword(callback, name: str) -> bool:
    """Return whether *callback* explicitly accepts a keyword or ``**kwargs``."""

    try:
        parameters = inspect.signature(callback).parameters.values()
    except (TypeError, ValueError):
        # Qt/builtin callables may not expose a Python signature. The bundled
        # controller supports the keyword, so preserve the current API path.
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == name
            and parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        )
        for parameter in parameters
    )


class SourceProject:
    def __init__(self, *, form, combo, source_edit, module_root, session, label_changed,
                 clear, invalidate, error, timings, append, refresh_catalog, dialog_parent):
        self._form = form
        self._combo = combo
        self._source_edit = source_edit
        self._module_root = module_root
        self._session = session
        self._label_changed = label_changed
        self._clear = clear
        self._invalidate = invalidate
        self._error = error
        self._timings = timings
        self._append = append
        self._refresh_catalog = refresh_catalog
        self._dialog_parent = dialog_parent
        self.project = None
        self.environment = None
        self.path = None
        self.target_refresh_requested = False

    def browse(self, _signal_value=None) -> None:
        path, _ = ask_file(self._dialog_parent, "Select source cds.lib", ["cds.lib (*)"])
        if path:
            # Check before changing the line edit.  Changing it first would
            # clear the current source catalog/context through the
            # ``textChanged`` handler even though this selection is rejected.
            if self.is_session_path(path):
                self.warn(path)
                return
            self._source_edit.setText(path)
            self.refresh(force_refresh=True)

    def project_changed(self, _index: int) -> None:
        """Resolve the selected module into this tab's source-only context."""

        if self._form.loading:
            return
        project_name = str(self._combo.currentData() or "").strip()
        if not project_name:
            if self.project is not None:
                self.project = None
                self.environment = None
                self._clear()
                self._source_edit.clear()
                self._invalidate("source project cleared")
                self._append("Project cleared; source workers use the launch environment")
            self._label_changed("")
            return
        previous_project = (
            "" if self.project is None else self.project.name
        )
        try:
            context = resolve_project(project_name, root=self._module_root)
            if self.is_session_path(context.cds_lib):
                self.warn(context.cds_lib)
                raise ValueError(self.warning_text(context.cds_lib))
        except Exception as exc:
            # Project selection is transactional.  A failed module must not
            # leave the old cds.lib paired with the launch environment (or a
            # different previously loaded project environment).
            self._form.loading = True
            try:
                previous_index = self._combo.findData(previous_project)
                self._combo.setCurrentIndex(max(previous_index, 0))
            finally:
                self._form.loading = False
            self._error(f"Project {project_name}: {exc}")
            return
        # Even two modules that resolve to the same cds.lib can define
        # different callbacks, model variables, and executable paths. Clear
        # page-owned source state before committing the new environment; the
        # process-level authoritative catalog cache remains reusable.
        self._clear()
        self._invalidate("source project changed")
        self.project = context
        self.environment = dict(context.environment)
        self._label_changed(context.name)
        # Setting source_edit invokes the established context invalidation
        # logic, which cancels stale defaults/generation work before refresh.
        self._source_edit.setText(str(context.cds_lib))
        self._append(
            f"Project loaded: {context.name}; default cds.lib: {context.cds_lib}"
        )
        self._timings(context)
        # A project selection may revisit a catalog already loaded in this
        # process. Reuse its catalog snapshot; the controller warms the new
        # project runtime. The toolbar Refresh still reloads catalog data.
        self.refresh()

    def select_project(self, project_name: str) -> None:
        """Load a persisted project name before restoring source cell state."""

        name = str(project_name).strip()
        if not name:
            self._form.loading = True
            try:
                self._combo.setCurrentIndex(0)
            finally:
                self._form.loading = False
            self.project = None
            self.environment = None
            self._label_changed("")
            return
        index = self._combo.findData(name)
        if index < 0:
            raise ValueError(
                f"configured project module is unavailable: {name!r}"
            )
        # Resolve first so a malformed/unloadable workspace project cannot
        # mutate the candidate page's visible selection before the load
        # transaction rejects that page.
        context = resolve_project(name, root=self._module_root)
        self._timings(context)
        self._form.loading = True
        try:
            self._combo.setCurrentIndex(index)
        finally:
            self._form.loading = False
        self.project = context
        self.environment = dict(context.environment)
        self._label_changed(context.name)

    def refresh(self, *, force_refresh: bool = False) -> None:
        """Load the selected source catalog.

        A user-initiated Refresh or Browse action passes
        ``force_refresh=True`` and bypasses the process cache.  Project
        selection and workspace restoration use the default cache path so an
        existing catalog can be reused while its project runtime starts.
        """
        path = self._source_edit.text().strip()
        if not path:
            return
        if self.is_session_path(path):
            self.warn(path)
            return
        self.path = Path(path).expanduser().resolve()
        request_options = {"force_refresh": True} if force_refresh else {}
        if self.environment is None:
            # Preserve the pre-project-selector call shape for manual mode.
            # External controller adapters and scripted GUI tests may still
            # implement the original one-argument method.
            refresh = self._refresh_catalog()
            if request_options and not _accepts_keyword(refresh, "force_refresh"):
                refresh(path)
            else:
                refresh(path, **request_options)
        else:
            self._refresh_catalog()(
                path,
                environment=self.environment,
                **request_options,
            )
        if self._session() is not None:
            # The controller is latest-request-only. Queue target refresh
            # until source catalog completion so the two operations cannot
            # cancel each other.
            self.target_refresh_requested = True

    @staticmethod
    def canonical_path(value: str | Path | None) -> Path | None:
        """Return a comparable absolute cds.lib path, or ``None`` for empty input.

        ``Path.resolve`` also follows symlinks, which matters when the user
        reaches the current session's file through a project alias or mount
        path.  Keep malformed/empty values out of the guard so normal request
        validation can report their more useful error later.
        """

        if value is None:
            return None
        try:
            raw = str(value).strip()
            if not raw:
                return None
            return Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def is_session_path(self, value: str | Path | None) -> bool:
        """Whether ``value`` names the cds.lib owned by this Virtuoso session."""

        session_target = getattr(self._session(), "target_cds_lib", None)
        source_path = self.canonical_path(value)
        target_path = self.canonical_path(session_target)
        return source_path is not None and target_path is not None and source_path == target_path

    def warning_text(self, value: str | Path) -> str:
        target = self.canonical_path(value) or Path(str(value))
        return (
            "The selected cds.lib is the library definition file used by the "
            "current Virtuoso session:\n\n"
            f"{target}\n\n"
            "Please choose a cds.lib from another technology or project."
        )

    def warn(self, value: str | Path) -> None:
        notice(self._dialog_parent, "Select an External Source cds.lib",
               self.warning_text(value))
