"""Launcher notice queues can wait for a busy Virtuoso without declaring EOF."""

import os
import time

import pytest

from sico.transport.relay import StdioLines


@pytest.mark.parametrize("close_while_full", [False, True])
def test_full_launcher_notice_queue_survives_its_former_timeout(close_while_full):
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as source:
        reader = StdioLines(source, backpressure=True)
        try:
            lines = [("notice-%d\n" % i).encode() for i in range(30)]
            os.write(write_fd, b"".join(lines))
            deadline = time.monotonic() + 2
            while not reader.lines.full():
                assert time.monotonic() < deadline
                time.sleep(0.005)
            assert not reader.closed.wait(1.2)
            if close_while_full:
                reader.close(timeout=1)
                assert not reader.thread.is_alive()
            else:
                assert [reader.next(timeout=2) for _ in lines] == lines
                assert not reader.closed.is_set()
                os.close(write_fd)
                write_fd = None
                assert reader.closed.wait(2)
        finally:
            reader.close(timeout=1)
            if write_fd is not None:
                os.close(write_fd)
