"""LSF execution evidence adapter; common BackgroundJobs owns admission and scheduling."""

from dataclasses import replace

from ..transport.framing import ProtocolError
from .background_worker import read_background_artifact
from .lsf_bundle import verified_receipt, worker_directory
from .lsf_evidence import load, save, scheduler, transaction


class LsfExecutionBackend:
    kind = 'lsf'

    def __init__(self, root, runtime=None, config=None, *, worker_options=None, scheduler=None):
        self.root, self.runtime = root, None if runtime is None else replace(runtime)
        self.config, self.scheduler = config, scheduler
        self.options = dict(worker_options or {})
        if config is not None:
            config.shared(root)

    @property
    def available(self):
        return self.runtime is not None and self.config is not None

    def create(self, request, progress):
        from .lsf_job import LsfJob

        if not self.available:
            raise ValueError('LSF execution is not configured')
        return LsfJob(self, request, progress)

    def observe(self, row):
        with transaction(self.root, row['job_id']):
            return self._observe(row)

    def _observe(self, row):
        record, config = load(self.root, row)
        observed = scheduler(config, self.scheduler).observe(record['scheduler'])
        if observed.get('job_id'):
            record['scheduler'] = observed
            if record['phase'] != 'cancel_requested':
                record['phase'] = 'associated'
            save(self.root, record)
        result = dict(row, scheduler=observed, automatic_resume_allowed=False,
                      scheduler_verified=False)
        if record['phase'] == 'cancel_requested':
            result['cancel_requested'] = True
        if observed['state'] in {'DONE', 'EXIT'}:
            try:
                receipt = verified_receipt(self.root, row, observed)
            except FileNotFoundError:
                receipt = None
            if receipt:
                result.update({k: v for k, v in receipt.items() if k != 'protocol'})
                result.update(execution_backend='lsf', scheduler=observed, scheduler_verified=True)
                if observed['state'] == 'EXIT' and receipt['state'] == 'completed':
                    result.update(state='running_unknown', reason='scheduler_exit_after_result',
                                  scheduler_verified=False)
            else:
                result.update(state='running_unknown', reason='scheduler_terminal_unverified')
        else:
            result.update(state='running_unknown' if observed['state'] in {'UNKWN', 'ZOMBI'}
                          else 'running' if observed['state'] == 'RUN' else 'queued')
        if result['state'] != 'completed':
            result.pop('artifact', None)
        return result

    def recover(self, row):
        try:
            result = self.observe(row)
        except FileNotFoundError:
            result = dict(row, state='running_unknown', reason='submission_evidence_missing')
        except ProtocolError:
            raise
        except (OSError, TimeoutError):
            result = dict(row, state='running_unknown', reason='scheduler_query_unconfirmed')
        result.update(recovered=True, automatic_resume_allowed=False)
        # The manager remains the single authoritative writer of its admission row.
        return result

    def cancel_retained(self, row):
        with transaction(self.root, row['job_id']):
            record, config = load(self.root, row)
            record['phase'] = 'cancel_requested'
            save(self.root, record)  # Lost bkill replies cannot erase the durable intent.
            outcome = scheduler(config, self.scheduler).cancel(record['scheduler'])
            return dict(self._observe(row), cancel_requested=True, cancel_outcome=outcome)

    def read_result(self, row):
        record, config = load(self.root, row)
        observed = scheduler(config, self.scheduler).observe(record['scheduler'])
        if observed['state'] != 'DONE':
            raise ValueError('Scheduler completion is not confirmed')
        receipt = verified_receipt(self.root, row, observed)
        if receipt is None or receipt.get('artifact') != row.get('artifact'):
            raise ProtocolError('Node result is not durably complete')
        try:
            return read_background_artifact(worker_directory(self.root, row['job_id']))
        except ProtocolError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise ProtocolError('Node result evidence changed during read') from exc
