"""Service imports stay single threaded until custody selects the service process."""

import json
import logging
import subprocess
import sys
import threading

from sico.service.diagnostics import DiagnosticQueue


def test_service_entry_imports_do_not_start_diagnostic_workers():
    code = '''
import json, threading
from sico.cli import main
from sico.service.service_entry import run_service
print(json.dumps([thread.name for thread in threading.enumerate()]))
'''
    result = subprocess.run([sys.executable, '-B', '-s', '-c', code],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['MainThread']


def test_concurrent_first_submissions_share_one_logging_worker():
    diagnostics = DiagnosticQueue()
    records, received = [], threading.Event()

    class Handler(logging.Handler):
        def emit(self, record):
            records.append(record)
            if len(records) == 12:
                received.set()

    logger = logging.getLogger('service-diagnostic-startup')
    handler = Handler()
    logger.addHandler(handler)
    barrier = threading.Barrier(12)

    def submit(index):
        barrier.wait(timeout=5)
        diagnostics.submit(logger.name, str(index))

    threads = [threading.Thread(target=submit, args=(index,)) for index in range(12)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert received.wait(5)
        assert {int(record.getMessage()) for record in records} == set(range(12))
        assert len({record.thread for record in records}) == 1
        assert records[0].thread != threading.get_ident()
    finally:
        logger.removeHandler(handler)


def test_worker_start_failure_drops_diagnostic_and_next_submission_recovers(monkeypatch):
    diagnostics = DiagnosticQueue()
    start = threading.Thread.start

    def unavailable(_thread):
        raise RuntimeError("cannot start new thread")

    monkeypatch.setattr(threading.Thread, 'start', unavailable)
    diagnostics.submit('service-diagnostic-startup', 'unavailable')
    assert diagnostics.dropped == 1 and diagnostics.queue.empty()
    monkeypatch.setattr(threading.Thread, 'start', start)
    received = threading.Event()

    class Handler(logging.Handler):
        def emit(self, record):
            if record.getMessage() == 'recovered':
                received.set()

    logger = logging.getLogger('service-diagnostic-startup')
    handler = Handler()
    logger.addHandler(handler)
    try:
        diagnostics.submit(logger.name, 'recovered')
        assert received.wait(5)
    finally:
        logger.removeHandler(handler)
