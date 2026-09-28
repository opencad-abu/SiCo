"""gui workspace transaction cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
    ProcessPage,
)
from mtsnetlistor.model import SourceDesign  # noqa: E402


def test_workspace_replace_shuts_down_page_that_fails_during_apply(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected candidate page must not leave a live controller behind."""

    from mtsnetlistor.config import WorkspaceConfig, WorkspaceProcess
    from mtsnetlistor.model import NetlistRequest

    requests = []
    for index in (1, 2):
        cds = tmp_path / f"process{index}.cds.lib"
        cds.write_text(f"DEFINE source{index} ./source{index}\n", encoding="utf-8")
        requests.append(
            NetlistRequest(
                SourceDesign(cds, f"source{index}", f"cell{index}")
            ).validate()
        )
    workspace = WorkspaceConfig(
        tuple(
            WorkspaceProcess(f"Process{index}", request)
            for index, request in enumerate(requests, start=1)
        )
    )

    allocated: list[ProcessPage] = []
    shutdown: list[ProcessPage] = []
    real_init = ProcessPage.__init__
    real_shutdown = ProcessPage.shutdown

    window = MtsMainWindow(session=None)

    def capture_init(page, *args, **kwargs):
        real_init(page, *args, **kwargs)
        allocated.append(page)

    apply_count = 0

    def fail_second_apply(page, request, *, refresh_catalog=True):
        nonlocal apply_count
        apply_count += 1
        if apply_count == 2:
            raise ValueError("synthetic apply failure")

    def capture_shutdown(page):
        shutdown.append(page)
        real_shutdown(page)

    monkeypatch.setattr(ProcessPage, "__init__", capture_init)
    monkeypatch.setattr(ProcessPage, "_apply_request_config", fail_second_apply)
    monkeypatch.setattr(ProcessPage, "shutdown", capture_shutdown)
    try:
        with pytest.raises(ValueError, match="synthetic apply failure"):
            window._replace_workspace(workspace)
        # Both candidate pages were allocated before the second apply failed;
        # cleanup must include the page that raised as well as earlier pages.
        assert len(allocated) == 2
        assert shutdown == allocated
        assert len(window._pages) == 1
    finally:
        window.close()
        window.controller.close()


def test_workspace_replace_contains_post_commit_catalog_refresh_error(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.config import WorkspaceConfig, WorkspaceProcess
    from mtsnetlistor.model import NetlistRequest

    cds = tmp_path / "source.cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(cds, "source", "top")).validate()
    workspace = WorkspaceConfig((WorkspaceProcess("Process1", request),))

    def fail_refresh(_page):
        raise RuntimeError("synthetic catalog failure")

    monkeypatch.setattr(ProcessPage, "_refresh_source", fail_refresh)
    window = MtsMainWindow(session=None)
    try:
        # Refresh runs after the tab transaction commits.  It must be reported
        # in the affected page while the loaded request remains available.
        window._replace_workspace(workspace)
        assert len(window._pages) == 1
        page = window._pages[0]
        assert page.source_edit.text() == str(cds.resolve())
        assert "source catalog refresh failed" in page.log.toPlainText()
        assert "synthetic catalog failure" in page.log.toPlainText()
    finally:
        window.close()
        window.controller.close()
