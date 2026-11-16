"""Negotiation can wait in another process while Qt and waiter cancellation remain responsive."""

import time
from concurrent.futures import CancelledError

import pytest
from agent_service_lifecycle_fixtures import paused_service
from agent_worker_fixtures import heartbeat
from PyQt5.QtWidgets import QLabel

from sico.service.service_ready import probe_ready
from sico.service.service_startup import ServiceStartup


@pytest.mark.parametrize("cancel", [False, True])
def test_paused_capability_handshake_never_blocks_qt_or_publishes_late_ready(tmp_path, cancel):
    with paused_service(tmp_path, kind="hello") as (descriptor, entered, release):
        label = QLabel("opening")
        startup = ServiceStartup(tmp_path)
        try:
            assert entered.wait(5)
            heartbeat(label)
            assert not startup.ready.done() and not release.is_set()
            if cancel:
                startup.close()
                assert startup.wait(2)
                with pytest.raises(CancelledError):
                    startup.ready.result()
            release.set()
            if not cancel:
                assert startup.ready.result(3) == descriptor
            assert probe_ready(descriptor, deadline=time.monotonic() + 2) == descriptor
        finally:
            release.set()
            startup.close()
            assert startup.wait(3)
            label.close()
