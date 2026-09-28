"""Backend host client; discover once, stage privately, never retry a mutation."""

import os
import time

from ..providers.config import resolve_provider_config
from ..storage.journal import open_private
from ..transport.bridge_client import BridgeClient
from ..transport.bridge_identity import discovery_path
from ..transport.framing import strict_json
from .host_contract import host_identity
from .service_management import exchange_service
from .service_messages import ServiceRequest
from .service_startup import ServiceStartup


class HostServiceGateway:
    def __init__(self, project, context, *, environment=None, provider_config=None, timeout=30):
        self.project, self.context, self.timeout = project, context, timeout
        self.environment = dict(os.environ if environment is None else environment)
        self.provider_config = provider_config
        self.descriptor = None

    def _prepare(self):
        if self.descriptor is None:
            startup = ServiceStartup(self.project, timeout=self.timeout)
            try:
                self.descriptor = startup.ready.result(self.timeout)
            finally:
                startup.close()

    def submit(self, message):
        self._prepare()
        config = resolve_provider_config(self.provider_config, launch_dir=self.project,
                                         environment=self.environment)
        with BridgeClient.discover(self.project, self.context, timeout=self.timeout) as bridge:
            bridge.register_target(self.context)
            reference = bridge.stage_host_payload(dict(message=message,
                provider_config=config, environment=self.environment))
            request = ServiceRequest("host.submit", message["id"],
                                     dict(bridge=bridge.descriptor, **reference))
            return exchange_service(self.descriptor, request,
                                    deadline=time.monotonic() + self.timeout).status

    def query(self, input_id):
        self._prepare()
        # Tombstone discovery is enough to query the captured generation after host loss.
        with os.fdopen(open_private(discovery_path(self.project, self.context), os.O_RDONLY),
                       "rb") as stream:
            bridge = strict_json(stream.read(8193))
        request = ServiceRequest("host.input", input_id, dict(host=host_identity(bridge)))
        return exchange_service(self.descriptor, request,
                                deadline=time.monotonic() + self.timeout).status

    def close(self):
        self.environment.clear()
