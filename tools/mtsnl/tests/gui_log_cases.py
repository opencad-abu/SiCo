"""gui log cases regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.controller import ControllerState  # noqa: E402
from mtsnetlistor.gui.controller import CATALOG_LOG_PREFIX  # noqa: E402
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)


def test_gui_worker_logs_are_marshaled_and_filtered(application: QApplication) -> None:
    window = MtsMainWindow(session=None)
    try:
        # This method is called by the controller executor thread in
        # production; _poll_state is the GUI-thread handoff point.
        window._receive_worker_log("createNetlist started\nnoise omitted\n")
        assert "createNetlist" not in window.log.toPlainText()
        window._poll_state()
        rendered = window.log.toPlainText()
        assert "OCEAN: createNetlist started" in rendered
        assert "noise omitted" not in rendered
    finally:
        window.close()
        window.controller.close()


def test_gui_streams_safe_catalog_startup_output_and_redacts_protocol(
    application: QApplication,
) -> None:
    window = MtsMainWindow(session=None)
    try:
        messages = (
            "\\o Loading PDK interface libInit.il\n",
            "Checking out Cadence license feature\n",
            '{"schema_version":1,"libraries":[]}\n',
            "CDS_MPS_SESSION=host-session\n",
            "API_KEY=must-not-render\n",
            "PROJ_NAME=secret-environment-dump\n",
        )
        for message in messages:
            window._receive_worker_log(CATALOG_LOG_PREFIX + message)
        window._poll_state()

        rendered = window.log.toPlainText()
        assert "dbAccess: Loading PDK interface libInit.il" in rendered
        assert "dbAccess: Checking out Cadence license feature" in rendered
        assert "\\o" not in rendered
        assert "schema_version" not in rendered
        assert "CDS_MPS_SESSION" not in rendered
        assert "must-not-render" not in rendered
        assert "secret-environment-dump" not in rendered
    finally:
        window.close()
        window.controller.close()


def test_gui_drains_filtered_worker_logs_on_poll(
    application: QApplication,
):
    window = MtsMainWindow(session=None)
    try:
        window._receive_worker_log("ordinary transcript noise\n")
        window._receive_worker_log("\\o INFO createNetlist for source cell\n")
        window._receive_worker_log("\\# WARNING modelFile section missing\n")
        window._receive_worker_log("\\w \\p \\e \\r \\i \\a INFO repeated prefix\n")
        window._receive_state(
            ControllerState(token=1, stage="generation_ready", busy=False)
        )
        window._poll_state()

        rendered = window.log.toPlainText()
        assert "OCEAN: INFO createNetlist for source cell" in rendered
        assert "OCEAN: WARNING modelFile section missing" in rendered
        assert "OCEAN: INFO repeated prefix" in rendered
        assert "OCEAN: \\o" not in rendered
        assert "OCEAN: \\#" not in rendered
        assert "ordinary transcript noise" not in rendered
    finally:
        window.close()
        window.controller.close()


def test_source_catalog_diagnostics_are_rendered_without_cache_key_dump(
    application: QApplication, tmp_path: Path
) -> None:
    from cadview.catalog import Catalog
    from mtsnetlistor.catalog import CatalogResult

    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE source ./source\n", encoding="utf-8")
    result = CatalogResult(
        Catalog(cds, (), True, "dbAccess"),
        True,
        "dbAccess",
        (
            "source_catalog_cache=hit",
            "source_fingerprint_ms=1.234",
            "source_provider_ms=2.345",
            "source_total_ms=3.456",
        ),
    )
    window = MtsMainWindow(session=None)
    try:
        window.source_edit.setText(str(cds))
        window._receive_state(ControllerState(source_catalog=result))
        window._poll_state()
        rendered = window.log.toPlainText()
        assert "Source catalog: source_catalog_cache=hit" in rendered
        assert "source_fingerprint_ms=1.234" in rendered
        assert "source_provider_ms=2.345" in rendered
        assert "source_total_ms=3.456" in rendered
    finally:
        window.close()
        window.controller.close()


def test_gui_expands_manual_cleanup_publication_diagnostics(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.publish import PublicationBundleResult

    window = MtsMainWindow(session=None)
    try:
        evidence = tmp_path / "run" / "publish" / "nl2view.log"
        evidence.parent.mkdir(parents=True)
        evidence.write_text("importer failed", encoding="utf-8")
        result = PublicationBundleResult(
            status="manual_cleanup_required",
            target_library="target",
            target_cell="renamed",
            affected_views=(str(tmp_path / "target" / "renamed" / "spectreText"),),
            message="importer failed; automatic OA rollback is not qualified",
            diagnostics=(
                f"run_dir={tmp_path / 'run'}",
                "target=target/renamed",
                "failure_type=MtsNetlistorError",
            ),
            log_files=(str(evidence), str(tmp_path / "run" / "missing.log")),
            cleanup_instructions=("inspect only the listed target view(s)",),
        )
        window._append_publication_diagnostics(result)
        rendered = window.log.toPlainText()
        assert "Publication: manual_cleanup_required" in rendered
        assert "Affected target view:" in rendered
        assert "Publication evidence (present):" in rendered
        assert "Publication evidence (missing):" in rendered
        assert "Publication diagnostic: target=target/renamed" in rendered
        assert "ACTION: inspect only the listed target view(s)" in rendered
        assert "manual OA inspection" in window.statusBar().currentMessage()
    finally:
        window.close()
        window.controller.close()


def test_repeated_publication_state_is_logged_once(
    application: QApplication,
) -> None:
    from mtsnetlistor.publish import PublicationBundleResult

    window = MtsMainWindow(session=None)
    try:
        result = PublicationBundleResult(
            status="manual_cleanup_required",
            target_library="target",
            target_cell="top",
            message="inspect target",
        )
        state = ControllerState(
            token=7,
            stage="publication_ready",
            publication=result,
            busy=False,
        )
        window._receive_state(state)
        window._poll_state()
        window._receive_state(state)
        window._poll_state()
        assert window.log.toPlainText().count("Publication: manual_cleanup_required") == 1
    finally:
        window.close()
        window.controller.close()
