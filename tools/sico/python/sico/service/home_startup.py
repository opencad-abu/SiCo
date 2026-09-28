"""Prepare a project desktop with no model configuration or design session."""

from .frontend_startup import FrontendStartup
from .remote_frontend import RemoteFrontend
from .service_startup import ServiceStartup


class HomeStartup(FrontendStartup):
    def _prepare(self, resources):
        self._claim_window(resources, "")
        self.configuration.set_result(None)
        self.phase = "discovery"
        startup = ServiceStartup(self.args.launch_dir)
        resources.callback(startup.close)
        descriptor = self._await(startup.ready)
        api = RemoteFrontend(descriptor, self.args.launch_dir, "")
        resources.callback(api.close_desktop)
        # Establish an authenticated frontend lane without creating a model or session.
        self._await(api.open_home())
        return api
