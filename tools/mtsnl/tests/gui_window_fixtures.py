"""Shared Qt application and immutable test inputs for process-page regressions."""

from __future__ import annotations
import os
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt5")

from pathlib import Path
from types import SimpleNamespace
from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidgetItem,
)
from mtsnetlistor.gui.main_window import (  # noqa: E402
    MtsMainWindow,
)
from mtsnetlistor.gui.cell_drafts import (
    CellDraft, CellIdentity, PublicationSelection, SimulatorDraft,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _draft(library, cell, view, *, dialect="spectre", **values):
    publication = {key: values.pop(key) for key in tuple(values)
                   if key in PublicationSelection.__dataclass_fields__}
    for name in ("temp", "scale", "gmin"):
        if name in values:
            values[name] = "" if values[name] is None else str(values[name])
    return CellDraft(CellIdentity(library, cell, view), dialect,
                     SimulatorDraft(**values), PublicationSelection(**publication))


def _view(window, key, dialect=None):
    return window.drafts.view(key, dialect or window._active_dialect)


def _text(window, key):
    state = _view(window, key).simulator
    return state.temp, state.scale, state.gmin


def _append_source_cell(window: MtsMainWindow, state: CellDraft) -> QListWidgetItem:
    window.drafts.add(state.key, state.dialect, state.simulator, state.publication)
    item = QListWidgetItem("/".join(state.key))
    item.setData(Qt.UserRole, state.key)
    window.source_cells_list.addItem(item)
    return item


def _generated_artifact(path: Path, digest: str = "digest") -> SimpleNamespace:
    path.write_text("subckt generated\nends generated\n", encoding="ascii")
    return SimpleNamespace(
        request_digest=digest,
        stable_output=path,
        run_dir=path.parent,
    )
