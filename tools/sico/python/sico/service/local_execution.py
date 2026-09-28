"""Local saved-OA execution adapter over the existing worker and evidence workflow."""

from dataclasses import replace

from .background_recovery import recover_record
from .background_worker import BackgroundWorker, read_background_artifact


class _LocalJob:
    def __init__(self, worker, request):
        self._worker, self._request = worker, request

    def run(self):
        return self._worker.run(self._request.method, dict(self._request.params))

    def cancel(self):
        self._worker.cancel()

    def detach(self):
        # Local execution belongs to this process and must be stopped on close.
        self.cancel()


class LocalExecutionBackend:
    kind = "local"

    def __init__(self, root, runtime, *, worker_options=None):
        self.root = root
        self.runtime = None if runtime is None else replace(runtime)
        self.options = dict(worker_options or {})

    @property
    def available(self):
        return self.runtime is not None

    def create(self, request, progress):
        if not self.available:
            raise ValueError("background_unavailable: 未配置后台运行环境")
        worker = BackgroundWorker(self.root / "workers", self.runtime, progress=progress,
            job_id=request.job_id, worker_id=request.worker_id,
            environment=dict(request.environment), **self.options)
        worker.process_session = str(self.root.parent / "sessions" / request.owner_session_id)
        return _LocalJob(worker, request)

    def recover(self, row):
        return recover_record(self.root / "records", self.root / "workers", row)

    def read_result(self, row):
        return read_background_artifact(self.root / "workers" / row["job_id"])
