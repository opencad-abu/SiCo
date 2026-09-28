"""Prepare one selected platform session without owning the existing desktop."""

from ..transport.framing import ProtocolError
from .frontend_startup import FrontendStartup


class PlatformStartup(FrontendStartup):
    def __init__(self, api, candidate, args, *, environment=None):
        self.api, self.candidate = api, candidate
        super().__init__(args, lambda *_a, **_k: None, environment=environment)

    def _prepare(self, resources):
        config = self._configuration()
        self.phase = "attachment"
        frontend = self._await(self.api.platforms.open(self.candidate, config, self.environment))
        try:
            self._await(self.api.control.request(frontend.session, "acquire"))
        except ProtocolError:
            raise
        except ValueError:
            pass  # An already owned design opens as an observer.
        return frontend
