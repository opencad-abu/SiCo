"""Host receipts remain ordered and bounded even when the parent stops reading."""

import json
import os
import threading

import pytest
import sico.service.notices as notices_module
from sico.service.notices import HostNotices


@pytest.fixture
def pipe():
    reader, writer = os.pipe()
    try:
        yield reader, writer
    finally:
        os.close(reader)
        os.close(writer)


def fill_pipe(writer):
    os.set_blocking(writer, False)
    try:
        while True:
            os.write(writer, b"x" * 4096)
    except BlockingIOError:
        pass
    finally:
        os.set_blocking(writer, True)


def drain(reader):
    os.set_blocking(reader, False)
    data = bytearray()
    while True:
        try:
            chunk = os.read(reader, 65536)
        except BlockingIOError:
            return bytes(data)
        if not chunk:
            return bytes(data)
        data.extend(chunk)


def test_all_notices_preserve_order_and_drain_before_close(pipe):
    reader, writer = pipe
    notices = HostNotices(writer)
    messages = [dict(kind=kind, **{key: key for key in fields})
                for kind, fields in HostNotices.FIELDS.items()]
    try:
        for message in messages:
            assert notices(**message)
        notices.close()
        assert notices.wait(2)
        assert not notices.failed.is_set(), notices.error
        assert [json.loads(line) for line in drain(reader).splitlines()] == messages
        assert not notices("shown", session_id="session")
        assert os.get_blocking(writer)
        os.write(writer, b"still owned by caller")
        assert drain(reader) == b"still owned by caller"
    finally:
        notices.close()
        assert notices.wait(2)


def test_encoding_and_partial_writes_run_only_on_writer_thread(pipe, monkeypatch):
    reader, writer = pipe
    owner = threading.get_ident()
    encoded, written = [], []
    encode, write = notices_module.json.dumps, os.write

    def format_message(message, **kwargs):
        encoded.append(threading.get_ident())
        assert encoded[-1] != owner
        return encode(message, **kwargs)

    def partial_write(fd, data):
        written.append(threading.get_ident())
        assert written[-1] != owner
        return write(fd, data[:7])

    monkeypatch.setattr(notices_module.json, "dumps", format_message)
    monkeypatch.setattr(notices_module.os, "write", partial_write)
    notices = HostNotices(writer)
    try:
        assert notices("accepted", id="request")
        notices.close()
        assert notices.wait(2)
        assert not notices.failed.is_set(), notices.error
        assert json.loads(drain(reader)) == {"kind": "accepted", "id": "request"}
        assert len(encoded) == 1 and len(written) > 1
        assert set(encoded) == set(written)
    finally:
        notices.close()
        assert notices.wait(2)


def test_pipe_backpressure_recovers_after_timeout_without_losing_receipts(pipe):
    reader, writer = pipe
    fill_pipe(writer)
    notices = HostNotices(writer, write_timeout=0.1)
    try:
        assert notices("shown", session_id="session")
        assert notices.stalled.wait(2)
        assert not notices.failed.is_set()
        assert notices("accepted", id="request")
        assert notices("reconnect", id="target")
        before = drain(reader)
        notices.close()
        assert notices.wait(2)
        assert not notices.failed.is_set() and not notices.stalled.is_set()
        rows = (before + drain(reader)).lstrip(b"x").splitlines()
        assert [json.loads(line) for line in rows] == [
            {"kind": "shown", "session_id": "session"},
            {"kind": "accepted", "id": "request"},
            {"kind": "reconnect", "id": "target"},
        ]
    finally:
        notices.close()
        assert notices.wait(2)


def test_timeout_after_partial_notice_resumes_exact_remaining_bytes(pipe, monkeypatch):
    reader, writer = pipe
    paused, release = threading.Event(), threading.Event()
    write = os.write

    def blocked_write(fd, data):
        if not paused.is_set():
            count = write(fd, data[:9])
            paused.set()
            return count
        if not release.is_set():
            raise BlockingIOError()
        return write(fd, data[:7])

    monkeypatch.setattr(notices_module.os, "write", blocked_write)
    # The artificial backpressure needs a small wait even though the real
    # pipe is writable, matching select's behavior with an actually full pipe.
    monkeypatch.setattr(notices_module.select, "select", lambda *args: release.wait(0.01))
    notices = HostNotices(writer, write_timeout=0.03)
    try:
        assert notices("accepted", id="request")
        assert notices.stalled.wait(2)
        assert notices("reconnect", id="target")
        release.set()
        notices.close()
        assert notices.wait(2) and not notices.failed.is_set()
        assert [json.loads(line) for line in drain(reader).splitlines()] == [
            {"kind": "accepted", "id": "request"}, {"kind": "reconnect", "id": "target"},
        ]
    finally:
        release.set()
        notices.close()
        notices.wait(2)


def test_slow_parent_resumes_without_losing_receipts(pipe, monkeypatch):
    reader, writer = pipe
    fill_pipe(writer)
    attempted = threading.Event()
    write = os.write

    def observe_write(fd, data):
        attempted.set()
        return write(fd, data)

    monkeypatch.setattr(notices_module.os, "write", observe_write)
    notices = HostNotices(writer)
    try:
        assert notices("accepted", id="first")
        assert notices("released", target_id="first")
        assert attempted.wait(2)
        before = drain(reader)
        notices.close()
        assert notices.wait(2)
        assert not notices.failed.is_set(), notices.error
        data = (before + drain(reader)).lstrip(b"x")
        assert [json.loads(line) for line in data.splitlines()] == [
            {"kind": "accepted", "id": "first"}, {"kind": "released", "target_id": "first"},
        ]
    finally:
        notices.close()
        assert notices.wait(2)


def test_overflow_fails_channel_instead_of_silently_dropping_acknowledgments(pipe, monkeypatch):
    _, writer = pipe
    entered, release = threading.Event(), threading.Event()
    encode = notices_module.json.dumps

    def slow_encode(message, **kwargs):
        entered.set()
        assert release.wait(3)
        return encode(message, **kwargs)

    monkeypatch.setattr(notices_module.json, "dumps", slow_encode)
    notices = HostNotices(writer, capacity=2)
    try:
        assert notices("shown", session_id="session")
        assert entered.wait(2)
        assert notices("accepted", id="first")
        assert notices("accepted", id="second")
        assert not notices("accepted", id="third")
        assert notices.failed.is_set()
        assert "queue is full" in notices.error
    finally:
        release.set()
        notices.close()
        assert notices.wait(2)


def test_shutdown_has_a_shorter_drain_budget_than_normal_writes(pipe):
    _, writer = pipe
    fill_pipe(writer)
    notices = HostNotices(writer, write_timeout=10)
    try:
        assert notices("accepted", id="request")
        notices.close(timeout=0.1)
        notices.close(timeout=10)
        assert notices.wait(2)
        assert notices.failed.is_set()
        assert "before shutdown" in notices.error
    finally:
        notices.close()
        assert notices.wait(2)


def test_broken_pipe_is_published_as_a_lifecycle_failure():
    reader, writer = os.pipe()
    os.close(reader)
    notices = HostNotices(writer)
    try:
        assert notices("shown", session_id="session")
        assert notices.failed.wait(2)
        assert notices.error
        assert notices.wait(2)
    finally:
        notices.close()
        assert notices.wait(2)
        os.close(writer)


@pytest.mark.parametrize("message", [
    {"kind": "unknown"}, {"kind": "accepted"}, {"kind": "shown", "id": "unexpected"},
    {"kind": "accepted", "id": "x" * 97}, {"kind": "accepted", "id": "bad\nline"},
    {"kind": "accepted", "id": {"mutable": "payload"}},
])
def test_notice_payloads_are_bounded_scalar_identities(pipe, message):
    reader, writer = pipe
    notices = HostNotices(writer)
    try:
        with pytest.raises(ValueError):
            notices(**message)
        notices.close()
        assert notices.wait(2)
        assert not notices.failed.is_set()
        assert drain(reader) == b""
    finally:
        notices.close()
        assert notices.wait(2)
