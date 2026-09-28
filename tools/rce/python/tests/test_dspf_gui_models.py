from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt

from dspf_gui_test_support import application
from rcepy.dspf_gui.models import NET_COLUMNS, PagedRepositoryModel


class FakeRepository:
    rows = [
        {"id": number, "name": f"N{number:04d}", "declared_cap": number * 1e-15}
        for number in range(450)
    ]
    calls: list[tuple[int, int]] = []

    def __init__(self, _path) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass

    def _filtered(self, search, exact_name=None):
        if exact_name is not None:
            return [row for row in self.rows if row["name"] == exact_name]
        return [row for row in self.rows if not search or search in row["name"]]

    def count_nets(self, search=None, *, exact_name=None) -> int:
        return len(self._filtered(search, exact_name))

    def list_nets(
        self, *, offset=0, limit=200, search=None, exact_name=None,
        sort_by="name", descending=False,
    ):
        self.calls.append((offset, limit))
        rows = sorted(
            self._filtered(search, exact_name),
            key=lambda row: row[sort_by],
            reverse=descending,
        )
        return rows[offset : offset + limit]


def make_model() -> PagedRepositoryModel:
    application()
    FakeRepository.calls.clear()
    model = PagedRepositoryModel(
        NET_COLUMNS,
        "count_nets",
        "list_nets",
        count_keys=("search", "exact_name"),
        list_keys=("search", "exact_name", "sort_by", "descending"),
        repository_factory=FakeRepository,
    )
    model.query.update(search=None, exact_name=None, sort_by="name", descending=False)
    model.set_index("unused.sqlite3")
    return model


def test_model_retains_only_one_sql_page() -> None:
    model = make_model()
    assert model.total_rows == 450
    assert model.rowCount() == 200
    assert FakeRepository.calls == [(0, 200)]

    model.set_page(2)
    assert model.rowCount() == 50
    assert model.row_at(0)["name"] == "N0400"
    assert FakeRepository.calls[-1] == (400, 200)


def test_model_pushes_search_and_sort_to_repository() -> None:
    model = make_model()
    model.set_query(search="N01")
    assert model.total_rows == 100
    assert all("N01" in row["name"] for row in model.rows)

    model.sort(0, Qt.DescendingOrder)
    assert model.row_at(0)["id"] == 199
    assert len(model.rows) == 100


def test_model_exact_name_bypasses_substring_page() -> None:
    model = make_model()
    model.set_query(search=None, exact_name="N0400")
    assert model.total_rows == 1
    assert model.row_at(0)["id"] == 400
