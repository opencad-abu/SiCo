"""Private side channel for SKILL stages, including failed IPC reply writes."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from ..storage.journal import open_private, sync_directory
from .framing import strict_json
from .relay import emit_diagnostic

PROTOCOL = 'cad_ai_skill_stage.v1'
EVENTS = frozenset('skill.' + name for name in (
    'request_parsed', 'request_overflow', 'request_rejected', 'source_spooled',
    'evaluation_started', 'evaluation_finished', 'evaluation_failed',
    'reply_prepared', 'reply_write_started', 'reply_write_succeeded', 'reply_write_failed'))


def stage_record(event, identity):
    if event.get('protocol') != PROTOCOL or event.get('event') not in EVENTS:
        return None
    if any(event.get(key) != value for key, value in identity.items()):
        return None
    result = dict(protocol=PROTOCOL, **identity, event=event['event'])
    for key in ('request_id', 'method', 'target_id', 'code'):
        value = event.get(key)
        if not isinstance(value, str) or len(value) > 160:
            return None
        result[key] = value
    for key in ('sequence', 'field_count', 'payload_length'):
        value = event.get(key)
        if type(value) is not int or not 0 <= value <= 2**63 - 1:
            return None
        result[key] = value
    return result


class SkillDiagnostics:
    def __init__(self, path, router):
        self.path, self.router = Path(path), router
        fd = open_private(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        self.stream = os.fdopen(fd, 'rb')
        try:
            sync_directory(self.path.parent)
        except BaseException:
            self.stream.close()
            raise
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def configuration(self):
        identity = self.router.journal.identity
        return '@diagnostics {} {} {}\n'.format(
            str(self.path).encode().hex(), identity['bridge_id'], identity['router_id'])

    def poll(self):
        with self._lock:
            for _ in range(256):
                offset = self.stream.tell()
                raw = self.stream.readline(8193)
                if not raw:
                    break
                if len(raw) > 8192:
                    raise ValueError('SKILL diagnostic line exceeds limit')
                if not raw.endswith(b'\n'):
                    self.stream.seek(offset)
                    break
                # The SKILL writer drains its port. Sync here before persisting evidence.
                os.fsync(self.stream.fileno())
                event = strict_json(raw)
                if not self.router.record_skill_stage(event):
                    emit_diagnostic(self.router.diagnostic, 'skill.stage_rejected')

    def _run(self):
        while not self._stopped.wait(.05):
            try:
                self.poll()
            except (OSError, ValueError, RuntimeError) as exc:
                emit_diagnostic(self.router.diagnostic, 'skill.stage_monitor_failed',
                                code=type(exc).__name__)
                return

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, name='skill-stages', daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=2)
        try:
            self.poll()
        except (OSError, ValueError, RuntimeError) as error:
            emit_diagnostic(self.router.diagnostic, 'skill.stage_monitor_failed',
                            code=type(error).__name__)
        finally:
            self.stream.close()
