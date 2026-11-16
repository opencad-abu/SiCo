"""Compiled slots must tolerate the payload Qt delivers with their signal.

Cython ``binding`` methods expose no inspectable signature, so PyQt cannot
reduce the emitted arguments the way it does for pure-Python callables.  A
zero-argument handler connected straight to ``destroyed(QObject*)`` or
``clicked(bool)`` works from source and crashes in the native build; these
tests pin the contract.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QObject  # noqa: E402

from sico_ui.receipts import DataReceipt  # noqa: E402


class _CapturedSignal:
    def __init__(self):
        self.slot = None

    def connect(self, slot):
        self.slot = slot


class _FakeParent(QObject):
    destroyed = _CapturedSignal()


def test_data_receipt_destroy_handler_accepts_the_qt_payload():
    parent = _FakeParent()
    receipt = DataReceipt(parent)
    assert parent.destroyed.slot is not None
    parent.destroyed.slot(parent)  # Qt emits destroyed(QObject*)
    assert receipt.pending is None
