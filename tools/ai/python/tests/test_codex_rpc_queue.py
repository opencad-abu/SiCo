"""Codex message admission includes both item count and total frame bytes."""

import queue
import threading
import time

import pytest

from sico.codex.rpc_queue import BoundedMessageDeque, BoundedMessageQueue, message_size


def test_queue_enforces_count_and_byte_budgets_without_dropping():
    messages = BoundedMessageQueue(maxsize=2, max_bytes=100)
    first, second = {"value": "a" * 60}, {"value": "b" * 60}
    messages.put(first)
    with pytest.raises(queue.Full):
        messages.put_nowait(second)
    assert messages.get_nowait() == first
    assert messages.queued_bytes == 0
    messages.put_nowait(second)
    assert messages.queued_bytes == message_size(second)
    assert messages.get_nowait() == second


def test_single_oversized_frame_can_be_observed_then_rejected_by_protocol():
    messages = BoundedMessageQueue(maxsize=2, max_bytes=64)
    oversized = {"value": "x" * 80}
    messages.put_nowait(oversized)
    assert messages.queued_bytes > messages.max_bytes
    assert messages.full()
    with pytest.raises(queue.Full):
        messages.put_nowait({"value": "y"})
    assert messages.get_nowait() == oversized


def test_byte_blocked_producer_wakes_after_consumer_releases_capacity():
    messages = BoundedMessageQueue(maxsize=8, max_bytes=100)
    messages.put_sized({"value": "held"}, 80)
    entered, finished = threading.Event(), threading.Event()

    def produce():
        entered.set()
        messages.put_sized({"value": "next"}, 40, timeout=2)
        finished.set()

    worker = threading.Thread(target=produce)
    worker.start()
    assert entered.wait(1)
    time.sleep(.05)
    assert not finished.is_set()
    assert messages.get_nowait() == {"value": "held"}
    assert finished.wait(1)
    worker.join(1)
    assert not worker.is_alive()
    assert messages.get_nowait() == {"value": "next"}
    assert messages.queued_bytes == 0


def test_deferred_messages_have_an_independent_byte_limit():
    pending = BoundedMessageDeque(100)
    first = {"value": "a" * 60}
    pending.append(first)
    with pytest.raises(queue.Full):
        pending.append({"value": "b" * 60})
    assert pending.popleft() == first
    pending.append({"value": "b" * 60})
    assert len(pending) == 1 and pending.queued_bytes > 0
    pending.clear()
    assert not pending and pending.queued_bytes == 0


def test_deferred_bulk_extension_is_all_or_nothing():
    pending = BoundedMessageDeque(100)
    with pytest.raises(queue.Full):
        pending.extend([{"value": "a" * 60}, {"value": "b" * 60}])
    assert not pending and pending.queued_bytes == 0
