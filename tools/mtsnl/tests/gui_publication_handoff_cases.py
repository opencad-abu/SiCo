"""gui publication handoff cases regressions."""

from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)


def test_run_without_publication_only_starts_generation(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "generate",
            lambda request: captured.update(request=request) or 41,
        )
        window._run()
        assert captured["request"].target.generate_netlist_view is False
        assert window.generation.pending[41].publication is None
    finally:
        window.close()
        window.controller.close()


def test_run_freezes_publication_and_continues_after_generation(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult
    from mtsnetlistor.config import canonical_request_digest

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")

    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.publish_text.setChecked(True)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.target_cell.setText("frozen_top")
        target_catalog = CatalogResult(
            Catalog(target_cds, (CatalogLibrary("target", target, True, (), target),), True, "dbAccess"),
            True,
            "dbAccess",
        )
        window.controller._state = ControllerState(target_catalog=target_catalog, busy=False)
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "generate",
            lambda request: captured.update(request=request) or 41,
        )
        published = []
        monkeypatch.setattr(
            window.controller,
            "publish",
            lambda request, **kwargs: published.append((request, kwargs)) or 42,
        )
        window._run()
        frozen = window.generation.pending[41].publication
        assert frozen.target.cell == "frozen_top"

        # A later target edit must not alter this Run's frozen publication.
        window.target_cell.setText("edited_after_run")
        artifact = SimpleNamespace(
            request_digest=canonical_request_digest(captured["request"]),
            stable_output=tmp_path / "top.spe",
            run_dir=tmp_path / "run",
        )
        artifact.stable_output.write_text("subckt top\nends top\n", encoding="utf-8")
        artifact.run_dir.mkdir()
        ready = ControllerState(
            token=41,
            generation=artifact,
            target_catalog=target_catalog,
            busy=False,
        )
        window._receive_state(ready)
        window._poll_state()
        QApplication.processEvents()
        assert published
        assert published[0][0].target.cell == "frozen_top"
    finally:
        window.close()
        window.controller.close()


def test_run_cancel_before_queued_publication_does_not_publish(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cadview.catalog import Catalog, CatalogLibrary
    from mtsnetlistor.catalog import CatalogResult
    from mtsnetlistor.config import canonical_request_digest

    cds = tmp_path / "source.cds.lib"
    source = tmp_path / "source"
    source.mkdir()
    cds.write_text(f"DEFINE source {source}\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text(f"DEFINE target {target}\n", encoding="utf-8")
    target_catalog = CatalogResult(
        Catalog(
            target_cds,
            (CatalogLibrary("target", target, True, (), target),),
            True,
            "dbAccess",
        ),
        True,
        "dbAccess",
    )
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window.source_library.setText("source")
        window.source_cell.setText("top")
        window.source_view.setText("schematic")
        window.publish_text.setChecked(True)
        window.target_library.addItem("target")
        window.target_library.setCurrentText("target")
        window.controller._state = ControllerState(
            target_catalog=target_catalog,
            busy=False,
        )
        captured = {}
        monkeypatch.setattr(
            window.controller,
            "generate",
            lambda request: captured.update(request=request) or 51,
        )
        published = []
        monkeypatch.setattr(
            window.controller,
            "publish",
            lambda request, **kwargs: published.append((request, kwargs)),
        )
        window._run()
        artifact = SimpleNamespace(
            request_digest=canonical_request_digest(captured["request"]),
            stable_output=tmp_path / "top.spe",
            run_dir=tmp_path / "run",
        )
        artifact.stable_output.write_text("subckt top\nends top\n", encoding="utf-8")
        artifact.run_dir.mkdir()
        window._receive_state(
            ControllerState(
                token=51,
                generation=artifact,
                target_catalog=target_catalog,
                busy=False,
            )
        )
        window._poll_state()
        # The generation-ready poll only queues a zero-delay callback. Cancel
        # must invalidate that callback before the event loop runs it.
        window._cancel()
        QApplication.processEvents()
        assert published == []
        assert window.generation.handoff is None
    finally:
        window.close()
        window.controller.close()
