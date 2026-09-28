"""gui source guard cases regressions."""

from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
import pytest
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.model import SourceDesign  # noqa: E402


def test_source_cds_lib_guard_rejects_current_session_file_before_refresh(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The source worker must always use a different cds.lib domain."""

    target = tmp_path / "target.cds.lib"
    target.write_text("DEFINE target ./target\n", encoding="utf-8")
    warnings = []
    calls = []
    window = MtsMainWindow(session=None)
    try:
        # Keep target-catalog startup out of this focused GUI test while still
        # exercising the same session descriptor boundary used in production.
        window.session = SimpleNamespace(target_cds_lib=target)
        monkeypatch.setattr(
            window.controller,
            "refresh_source_catalog",
            lambda path, **_kwargs: calls.append(path),
        )
        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.notice",
            lambda parent, title, message: warnings.append((title, message)),
        )
        window.source_edit.setText(str(target))
        window._refresh_source()

        assert calls == []
        assert len(warnings) == 1
        assert "current Virtuoso session" in warnings[0][1]
        assert "another technology or project" in warnings[0][1]
    finally:
        window.close()
        window.controller.close()


def test_source_cds_lib_guard_catches_symlink_alias_in_browse(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.cds.lib"
    target.write_text("DEFINE target ./target\n", encoding="utf-8")
    alias = tmp_path / "project-alias.cds.lib"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("filesystem does not support symlinks")
    warnings = []
    selected = []
    window = MtsMainWindow(session=None)
    try:
        window.session = SimpleNamespace(target_cds_lib=target)
        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.notice",
            lambda parent, title, message: warnings.append((title, message)),
        )
        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.ask_file",
            lambda *_args, **_kwargs: (str(alias), True),
        )
        monkeypatch.setattr(
            window.controller,
            "refresh_source_catalog",
            lambda path, **_kwargs: selected.append(path),
        )
        window._browse_source()

        assert selected == []
        assert warnings
        assert window.source_edit.text() == ""
    finally:
        window.close()
        window.controller.close()


def test_source_cds_lib_guard_rejects_loaded_config_before_mutating_form(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtsnetlistor.model import NetlistRequest

    target = tmp_path / "target.cds.lib"
    target.write_text("DEFINE target ./target\n", encoding="utf-8")
    request = NetlistRequest(SourceDesign(target, "target", "top")).validate()
    warnings = []
    window = MtsMainWindow(session=None)
    try:
        window.session = SimpleNamespace(target_cds_lib=target)
        before = window.source_edit.text()
        monkeypatch.setattr(
            "mtsnetlistor.gui.source_project.notice",
            lambda parent, title, message: warnings.append((title, message)),
        )
        with pytest.raises(ValueError, match="current Virtuoso session"):
            window._apply_request_config(request)
        assert window.source_edit.text() == before
        assert warnings
    finally:
        window.close()
        window.controller.close()
