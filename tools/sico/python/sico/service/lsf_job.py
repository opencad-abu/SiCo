"""One submission attempt followed by bounded observation of its captured identity."""

import threading
import time

from ..transport.framing import ProtocolError
from .lsf_bundle import prepare_bundle
from .lsf_evidence import initial, save, scheduler


class LsfJob:
    def __init__(self, backend, request, progress):
        self.backend, self.request, self.progress = backend, request, progress
        self._cancel, self._detach = threading.Event(), threading.Event()
        self._used = False

    def cancel(self):
        self._cancel.set()

    def detach(self):
        self._detach.set()

    def run(self):
        if self._used:
            raise RuntimeError('Scheduled execution cannot be replayed')
        self._used = True
        b, request = self.backend, self.request
        row = dict(job_id=request.job_id, worker_id=request.worker_id,
                   method=request.method, target={k: request.params.get(k, 'schematic')
                                                  for k in ('lib', 'cell', 'view')})
        manifest = prepare_bundle(b.root, request, b.runtime, dict(request.environment),
                                  b.options, b.config)
        record = initial(request, b.config, manifest)
        save(b.root, record)  # Commit intent before the only bsub attempt.
        self.progress(dict(row, state='starting', execution_backend='lsf'))
        if self._cancel.is_set() or self._detach.is_set():
            result = dict(row, state='cancelled', finished_at=time.time(),
                          execution_backend='lsf', operation_dispatched=False)
            self.progress(result)
            return result
        try:
            job_id = scheduler(b.config, b.scheduler).submit(record['scheduler']['name'], manifest)
        except (OSError, ValueError, RuntimeError, TimeoutError):
            # Malformed or missing acknowledgement cannot prove non-submission.
            pass
        else:
            record['scheduler'].update(job_id=job_id)
            record['phase'] = 'associated'
            save(b.root, record)
        while True:
            try:
                if self._cancel.is_set():
                    self._cancel.clear()
                    result = b.cancel_retained(row)
                else:
                    result = b.observe(row)
            except ProtocolError:
                raise
            except (OSError, ValueError, RuntimeError, TimeoutError):
                result = dict(row, state='running_unknown', reason='scheduler_query_unconfirmed')
            self.progress(result)
            if (result.get('scheduler_verified') or self._detach.is_set()
                    or result.get('scheduler', {}).get('state') in {'DONE', 'EXIT'}):
                return result
            self._detach.wait(b.config.poll_seconds)
