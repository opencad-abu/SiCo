"""The shared instance bridge accepts lifecycle commands on the private pipe."""

import os
import time
from types import SimpleNamespace

import pytest
from sico.service.launch import bridge_control
from sico.transport.relay import StdioLines


def test_binding_event_and_lifecycle_commands_share_pipe_without_reader_crash():
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    reader = StdioLines(source)
    events = []
    reader.control = bridge_control(reader, SimpleNamespace(native_binding_event=events.append))
    try:
        os.write(write_fd, b'@show\n@binding {"kind":"binding.native_closed"}\n@shutdown\n')
        assert reader.closed.wait(2)
        assert events == [{"kind": "binding.native_closed"}]
        assert reader.lines.empty()
        reader.thread.join(2)
        assert not reader.thread.is_alive()
    finally:
        reader.close()
        source.close()
        os.close(write_fd)


def test_desktop_attach_command_carries_only_a_valid_session_identity():
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    reader = StdioLines(source)
    events = []
    reader.control = events.append
    session_id = "a" * 32
    try:
        os.write(write_fd, b"@attach " + session_id.encode() + b"\n")
        deadline = time.monotonic() + 2
        while not events and time.monotonic() < deadline:
            time.sleep(0.01)
        assert events == [{"kind": "attach", "session_id": session_id}]
    finally:
        reader.close()
        source.close()
        os.close(write_fd)


@pytest.mark.parametrize("sequence", ["1", "2147483647", "0", "-1", "2147483648", "bad"])
def test_sequenced_attach_validates_order_and_original_input(sequence):
    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb")
    events = []
    reader = StdioLines(source, control=events.append)
    try:
        os.write(write_fd, ("@attach " + "a" * 32 + " original " + sequence + "\n").encode())
        os.close(write_fd)
        assert reader.closed.wait(2)
        assert events == ([dict(kind="attach", session_id="a" * 32,
                                input_id="original", ask_sequence=int(sequence))]
                          if sequence in {"1", "2147483647"} else [])
    finally:
        reader.close()
        source.close()
