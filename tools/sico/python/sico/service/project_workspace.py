"""Service-owned read services over the project's sole session registry."""

from contextlib import ExitStack

from .background_service import BackgroundService
from .catalog import CatalogService
from .cleanup_task import DEFAULT_CLEANUP_SECONDS, CleanupTask
from .event_service import EventService
from .workbench_data import WorkbenchService
from .worker import WorkerApi
from ..storage.roots import agent_root


class ProjectWorkspace:
    def __init__(self, owner):
        self.owner = owner
        self.root = agent_root(owner.project)
        with ExitStack() as pending:
            def own(service):
                pending.callback(self._stop, service)
                return service

            self.events = own(EventService())
            self.data = own(WorkbenchService())
            # Site settings belong to captured session dependencies, never this facade.
            self.background = own(BackgroundService(self.root / "background", config_path=""))
            self.catalog = own(CatalogService(self.root, lambda: tuple(owner.controllers.values())))
            self.worker = WorkerApi(self)
            pending.pop_all()
        self._cleanup = CleanupTask(self._release, "copilot-workspace-close")

    @staticmethod
    def _stop(service):
        service.close()
        service.wait()

    @property
    def controllers(self):
        return self.owner.controllers

    def _close_controllers(self):
        # WorkerApi compatibility hook: the project owner alone releases writers.
        pass

    def close(self, timeout=DEFAULT_CLEANUP_SECONDS):
        return self._cleanup.start().wait(timeout)

    def _release(self):
        self.worker.close()
        self.events.close()
        self.data.close()
        self.background.close()
        self.catalog.close()
        self.worker.wait()
        self.events.wait()
        self.data.wait()
        self.background.wait()
        self.catalog.wait()
