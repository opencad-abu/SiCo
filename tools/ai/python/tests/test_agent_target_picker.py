"""Linked library/cell/view picker used to open a design target window."""

from __future__ import annotations

import os
import threading

import pytest
from agent_gui_wait import receipts, wait_until

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QDialog

from sico.service.targets import TargetService
from sico_ui.target_picker import SCHEMATIC_FAMILY, TargetPicker


class Provider:
    def __init__(self, create=None):
        self.calls = []
        if create is not None:
            self.create = create

    def libraries(self, include_readonly=False):
        self.calls.append(("libraries", include_readonly))
        libraries = [{"name": "AnalogWork", "writable": True}]
        if include_readonly:
            libraries.append({"name": "smic28hkmg", "writable": False})
        return libraries

    def cells(self, library):
        self.calls.append(("cells", library))
        return ["amp_tb"] if library == "AnalogWork" else ["rppo_2t_ckt"]

    def views(self, library, cell, family=None):
        self.calls.append(("views", library, cell))
        return [
            {"view_name": "schematic", "oa_view_type": "schematic",
             "oa_view_type_source": "platform_api"},
            {"view_name": "av_extracted", "oa_view_type": "maskLayout",
             "oa_view_type_source": "platform_api"},
            {"view_name": "schematic_saved", "oa_view_type": "schematic",
             "oa_view_type_source": "platform_api", "open": True},
        ]

    def open_target(self, target):
        self.calls.append(("open", dict(target)))
        return {**target, "window": "win:3"}


@pytest.fixture
def make_picker():
    service = TargetService()
    widgets = []

    def create(provider):
        picker = TargetPicker(service.source(provider), SCHEMATIC_FAMILY)
        widgets.append(picker)
        return picker

    yield create
    for picker in widgets:
        picker.reject()
    service.close()
    assert service.wait(5)


def test_picker_links_lists_and_filters_the_editor_family(make_picker):
    provider = Provider()
    picker = make_picker(provider)
    receipts(picker)
    assert [picker.library.itemText(i) for i in range(picker.library.count())] == ["AnalogWork"]
    assert [picker.cell.itemText(i) for i in range(picker.cell.count())] == ["amp_tb"]
    views = [picker.view.itemText(i) for i in range(picker.view.count())]
    assert views == ["schematic", "schematic_saved（已打开）"]

    picker.view.setCurrentIndex(1)
    assert not picker.open_button.isEnabled()          # already open: nothing to bind anew
    picker.view.setCurrentIndex(0)
    assert picker.open_button.isEnabled()
    picker.read_only.setChecked(True)
    picker.accept()
    receipts(picker)
    assert picker.selection == {
        "library": "AnalogWork", "cell": "amp_tb", "view_name": "schematic",
        "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
        "read_only": True, "window": "win:3",
    }
    assert ("views", "AnalogWork", "amp_tb") in provider.calls


def test_picker_lists_read_only_libraries_on_demand(make_picker):
    provider = Provider()
    picker = make_picker(provider)
    receipts(picker)
    assert provider.calls[0] == ("libraries", False)
    picker.all_libraries.setChecked(True)
    receipts(picker)
    labels = [picker.library.itemText(i) for i in range(picker.library.count())]
    assert labels == ["AnalogWork", "smic28hkmg（只读）"]
    assert ("libraries", True) in provider.calls
    picker.library.setCurrentIndex(1)
    receipts(picker)
    assert [picker.cell.itemText(i) for i in range(picker.cell.count())] == ["rppo_2t_ckt"]


def test_picker_offers_creation_only_when_the_view_is_missing(make_picker):
    without_create = make_picker(Provider())
    receipts(without_create)
    assert without_create.create_button.isHidden() or not without_create.create_button.isEnabled()

    calls = []

    def create(target):
        calls.append(target)
        return {
            **target, "view_name": "schematic",
            "oa_view_type": "schematic",
            "oa_view_type_source": "platform_api",
            "window": "win:3",
        }

    class Empty(Provider):
        def views(self, library, cell, family=None):
            self.calls.append(("views", library, cell))
            return []

    picker = make_picker(Empty(create))
    receipts(picker)
    assert not picker.create_button.isHidden() and picker.create_button.isEnabled()
    assert not picker.open_button.isEnabled()
    picker.create_view()
    receipts(picker)
    assert calls and calls[0]["library"] == "AnalogWork" and calls[0]["cell"] == "amp_tb"
    assert picker.selection == {
        "library": "AnalogWork", "cell": "amp_tb", "view_name": "schematic",
        "oa_view_type": "schematic", "oa_view_type_source": "platform_api",
        "read_only": False, "window": "win:3",
    }


def test_slow_lookup_does_not_block_qt_and_cancel_discards_late_result(make_picker):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()

    class Slow(Provider):
        def libraries(self, include_readonly=False):
            assert threading.get_ident() != owner
            entered.set()
            assert release.wait(5)
            return super().libraries(include_readonly)

    provider = Slow()
    picker = make_picker(provider)
    picker.show()
    ticks, finished = [], []
    timer = QTimer(picker)
    timer.timeout.connect(lambda: ticks.append(True))
    picker.finished.connect(finished.append)
    timer.start(5)
    try:
        assert entered.wait(2)
        assert not picker.open_button.isEnabled()
        assert "正在加载" in picker.hint.text()
        QTest.qWait(60)
        assert len(ticks) >= 2
        picker.reject()
        assert finished == [QDialog.Rejected]
    finally:
        release.set()
        timer.stop()
    wait_until(lambda: bool(provider.calls))
    QTest.qWait(60)
    assert not picker.library.count()
    assert picker.selection is None
    assert all(call[0] != "cells" for call in provider.calls)


def test_rapid_library_changes_ignore_inflight_and_cancel_queued_lookups(make_picker):
    entered, release = threading.Event(), threading.Event()

    class Slow(Provider):
        def libraries(self, include_readonly=False):
            return [{"name": name, "writable": True} for name in ("old", "unused", "latest")]

        def cells(self, library):
            self.calls.append(("cells", library))
            if library == "old":
                entered.set()
                assert release.wait(5)
            return [library + "_cell"]

    provider = Slow()
    picker = make_picker(provider)
    try:
        wait_until(entered.is_set)
        picker.library.setCurrentIndex(1)
        picker.library.setCurrentIndex(2)
        assert picker.cell.count() == picker.view.count() == 0
    finally:
        release.set()
    receipts(picker)
    assert picker.cell.currentData() == "latest_cell"
    assert ("cells", "unused") not in provider.calls
    assert [call for call in provider.calls if call[0] == "views"] == [
        ("views", "latest", "latest_cell")]


def test_lookup_failure_keeps_a_retry_and_never_offers_create(make_picker):
    class Failing(Provider):
        broken = True

        def views(self, library, cell, family=None):
            if self.broken:
                raise RuntimeError("lookup unavailable")
            return super().views(library, cell, family)

    provider = Failing(create=lambda _target: {})
    picker = make_picker(provider)
    receipts(picker)
    assert "lookup unavailable" in picker.hint.text()
    assert not picker.create_button.isEnabled()
    assert not picker.open_button.isEnabled()
    assert not picker.retry.isHidden()
    provider.broken = False
    picker.retry.click()
    receipts(picker)
    assert picker.open_button.isEnabled()


def test_open_failure_allows_retry_and_success_closes_only_after_rpc(make_picker, tmp_path):
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()

    class Failing(Provider):
        broken = True

        def open_target(self, target):
            assert threading.get_ident() != owner
            self.calls.append(("attempt", dict(target)))
            if self.broken:
                raise RuntimeError("view is locked")
            entered.set()
            assert release.wait(5)
            return super().open_target(target)

    provider = Failing()
    picker = make_picker(provider)
    picker.show()
    receipts(picker)
    picker.accept()
    receipts(picker)
    assert picker.isVisible() and picker.selection is None
    assert picker.open_button.isEnabled() and "view is locked" in picker.hint.text()
    assert picker.grab().save(str(tmp_path / "target-open-error.png"))
    provider.broken = False
    picker.accept()
    try:
        assert entered.wait(2)
        assert picker.isVisible() and picker.selection is None
        assert not picker.library.isEnabled() and not picker.open_button.isEnabled()
        picker.accept()
        QTest.qWait(60)
        assert len([call for call in provider.calls if call[0] == "attempt"]) == 2
        assert picker.grab().save(str(tmp_path / "target-opening.png"))
    finally:
        release.set()
    receipts(picker)
    assert not picker.isVisible() and picker.selection["window"] == "win:3"


def test_cancel_during_open_does_not_accept_late_result(make_picker):
    entered, release = threading.Event(), threading.Event()

    class Slow(Provider):
        def open_target(self, target):
            entered.set()
            assert release.wait(5)
            return super().open_target(target)

    provider = Slow()
    picker = make_picker(provider)
    receipts(picker)
    finished = []
    picker.finished.connect(finished.append)
    picker.accept()
    try:
        assert entered.wait(2)
        picker.reject()
    finally:
        release.set()
    wait_until(lambda: any(call[0] == "open" for call in provider.calls))
    QTest.qWait(60)
    assert picker.selection is None and finished == [QDialog.Rejected]
