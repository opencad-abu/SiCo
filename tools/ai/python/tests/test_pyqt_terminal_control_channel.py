"""Control pipe decoding, read-error handling and one-way shutdown release."""

from __future__ import annotations

import errno

import pytest

from cad_ai_terminal import control_channel as module
from cad_ai_terminal.control_channel import ControlChannel


class Notifier:
    Read = 1

    def __init__(self, *_args):
        self.activated = type("Signal", (), {"connect": lambda _self, callback: setattr(self, "callback", callback)})()
        self.enabled = True
        self.deleted = 0

    def setEnabled(self, enabled):
        self.enabled = enabled

    def deleteLater(self):
        self.deleted += 1


def make_channel(monkeypatch, fd=123):
    monkeypatch.setattr(module, "parse_control_fd", lambda: fd)
    shown = []
    shutdown = []
    channel = ControlChannel(None, lambda: shown.append(True), lambda: shutdown.append(True), Notifier)
    return channel, shown, shutdown


def test_no_control_fd_is_inert_and_close_is_idempotent(monkeypatch):
    channel, shown, shutdown = make_channel(monkeypatch, None)
    channel._read_available()
    channel.close()
    channel.close()
    assert channel._notifier is None
    assert shown == [] and shutdown == []


def test_interrupted_and_partial_reads_are_retried_then_show(monkeypatch):
    channel, shown, shutdown = make_channel(monkeypatch)
    reads = iter([InterruptedError(), b"sh", b"ow\n", BlockingIOError()])

    def read(*_args):
        value = next(reads)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(module.os, "read", read)
    channel._read_available()
    assert shown == [] and shutdown == []
    channel._read_available()
    assert shown == [True] and shutdown == []


def test_eagain_returns_without_shutdown_and_eio_releases_once(monkeypatch):
    channel, shown, shutdown = make_channel(monkeypatch)
    errors = iter([OSError(errno.EAGAIN, "again"), OSError(errno.EIO, "bad")])
    monkeypatch.setattr(module.os, "read", lambda *_args: (_ for _ in ()).throw(next(errors)))
    closed = []
    monkeypatch.setattr(module.os, "close", lambda fd: closed.append(fd))
    channel._read_available()
    assert shutdown == [] and channel._fd == 123
    channel._read_available()
    assert shutdown == [True] and channel._fd is None and closed == [123]
    channel._request_shutdown()
    assert shutdown == [True]


def test_notifier_is_released_and_close_fd_errors_are_swallowed(monkeypatch):
    channel, _shown, _shutdown = make_channel(monkeypatch)
    notifier = channel._notifier

    def close_fails(_fd):
        raise OSError("already gone")

    monkeypatch.setattr(module.os, "close", close_fails)
    channel.close()
    assert notifier.enabled is False and notifier.deleted == 1
    assert channel._fd is None
