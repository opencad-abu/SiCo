"""Asynchronous batch facade; startup scan and all execution I/O run off Qt."""

import os
import threading
from pathlib import Path

from .workbench_data import WorkbenchService


class BackgroundService:
    """Future-based desktop facade: configuration, files and checks stay off Qt."""
    def __init__(self, root, *, runtime=None, config_path=None, **options):
        self.root, self.runtime, self.options = root, runtime, options
        self.config_path = (config_path if config_path is not None
                            else os.environ.get('SICO_BACKGROUND_CONFIG'))
        self._api = WorkbenchService()
        self._manager = None
        self._closing = False
        self._stopped = threading.Event()
        self._initial = self._api._request(self._get_manager)

    def _get_manager(self):
        if self._manager is None:
            from .background_config import load_settings
            from .background_jobs import BackgroundJobs
            from .lsf_execution import LsfExecutionBackend

            # One captured file read for both runtime and scheduler settings.
            runtime, lsf = load_settings(self.config_path)
            runtime = self.runtime or runtime
            options = dict(self.options)
            if lsf is not None:
                options.update(execution_backend='lsf', backends={'lsf': LsfExecutionBackend(
                    Path(self.root), runtime, lsf, worker_options=options.get('worker_options'))})
            self._manager = BackgroundJobs(self.root, runtime, **options)
        return self._manager

    def authorized(self, operation):
        if self._closing:
            raise ValueError('Background service is closing')
        return self._api._request(lambda: operation(self._get_manager()))

    def _call(self, method, args, kwargs, validate=None):
        if self._closing:
            raise ValueError('Background service is closing')
        if validate is not None:
            validate()
        result = getattr(self._get_manager(), method)(*args, **kwargs)
        if validate is not None:
            validate()
        return result

    def request(self, method, *args, validate=None, **kwargs):
        if self._closing:
            raise ValueError('Background service is closing')
        if method not in {'submit_background', 'get_background_status', 'cancel_background',
                          'read_background_result', 'list_background'}:
            raise ValueError('Unknown background operation')
        return self._api._request(self._call, method, args, kwargs, validate)

    @property
    def busy(self):
        return (not self._initial.done() or self._initial.exception() is not None
                or self._api.busy or bool(self._manager and self._manager.busy)
                or (self._closing and not self._stopped.is_set()))

    def close(self):
        if self._closing:
            return
        self._closing = True
        # Closing must not need a free queue slot or wait for filesystem work on Qt.
        self._api.close()
        threading.Thread(target=self._shutdown, name='background-shutdown', daemon=True).start()

    def _shutdown(self):
        try:
            self._api.wait()
            if self._manager:
                self._manager.close()
                self._manager.wait()
        finally:
            self._stopped.set()

    def wait(self, timeout=None):
        return self._stopped.wait(timeout)
