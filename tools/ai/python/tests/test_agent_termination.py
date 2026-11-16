"""Parent termination signals become one event and restore the host handlers."""

import os
import signal

import pytest
from sico.service.termination import SIGNALS, TerminationSignal


@pytest.mark.parametrize("number", SIGNALS)
def test_termination_signal_sets_event_and_restores_handler(number):
    previous = signal.getsignal(number)
    termination = TerminationSignal()
    try:
        with termination:
            os.kill(os.getpid(), number)
            assert termination.received.wait(1)
    finally:
        termination.restore()
    assert signal.getsignal(number) == previous
