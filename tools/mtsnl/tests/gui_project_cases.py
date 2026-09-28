"""gui project cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)


def test_project_selector_uses_isolated_source_environment(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.project import ProjectContext

    root = tmp_path / "modulefiles"
    root.mkdir()
    project_home = tmp_path / "project-home"
    project_home.mkdir()
    cds = project_home / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    context = ProjectContext(
        "proj2",
        root / "proj2",
        {"PROJ_NAME": "proj2", "PROJ_USER_HOME": str(project_home)},
        project_home,
        cds.resolve(),
        (
            ("modulecmd_purge_ms", 1.25),
            ("modulecmd_load_ms", 2.5),
            ("project_resolve_ms", 4.0),
        ),
    )
    calls = []
    window = MtsMainWindow(session=None, module_root=root)
    try:
        page = window._pages[0]
        page.project_combo.addItem("proj2", "proj2")
        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.resolve_project",
            lambda name, root=None: context,
        )
        monkeypatch.setattr(
            page.controller,
            "refresh_source_catalog",
            lambda path, **kwargs: calls.append((path, kwargs)),
        )
        page.project_combo.setCurrentText("proj2")

        assert page.source_edit.text() == str(cds.resolve())
        assert window.process_tabs.tabText(window.process_tabs.indexOf(page)) == "proj2"
        assert page._source_environment == context.environment
        assert calls == [(str(cds.resolve()), {"environment": context.environment})]
        rendered = page.log.toPlainText()
        assert "modulecmd_purge_ms=1.250" in rendered
        assert "modulecmd_load_ms=2.500" in rendered
        assert "project_resolve_ms=4.000" in rendered

        page.project_combo.setCurrentIndex(0)
        assert window.process_tabs.tabText(window.process_tabs.indexOf(page)) == "Process1"
    finally:
        window.close()
        window.controller.close()


def test_failed_project_change_restores_previous_project_context(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.project import ProjectContext

    root = tmp_path / "modulefiles"
    root.mkdir()
    first_home = tmp_path / "first-home"
    first_home.mkdir()
    first_cds = first_home / "cds.lib"
    first_cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    first = ProjectContext(
        "proj1",
        root / "proj1",
        {"PROJ_NAME": "proj1"},
        first_home,
        first_cds.resolve(),
    )
    window = MtsMainWindow(session=None, module_root=root)
    try:
        page = window._pages[0]
        page.project_combo.addItem("proj1", "proj1")
        page.project_combo.addItem("broken", "broken")

        def resolve(name, root=None):
            if name == "proj1":
                return first
            raise ValueError("synthetic module failure")

        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.resolve_project", resolve
        )
        monkeypatch.setattr(
            page.controller, "refresh_source_catalog", lambda *_args, **_kwargs: 1
        )
        page.project_combo.setCurrentText("proj1")
        page.project_combo.setCurrentText("broken")

        assert page.project_combo.currentData() == "proj1"
        assert page._project_context is first
        assert page._source_environment == first.environment
        assert page.source_edit.text() == str(first_cds.resolve())
        assert window.process_tabs.tabText(window.process_tabs.indexOf(page)) == "proj1"
        assert "synthetic module failure" in page.log.toPlainText()
    finally:
        window.close()
        window.controller.close()
