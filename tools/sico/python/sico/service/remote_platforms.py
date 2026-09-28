"""Frontend platform operations expose candidate IDs, never bridge credentials."""

import uuid

from ..transport.framing import ProtocolError
from .service_protocol import exact_fields


class RemotePlatforms:
    def __init__(self, api):
        self.api = api

    def instances(self, platform="CDNS-IC"):
        def publish(rows):
            if not isinstance(rows, list) or len(rows) > 32:
                raise ProtocolError("Invalid platform inventory")
            for row in rows:
                exact_fields(row, {"id", "platform", "instance", "generation", "project", "version", "node", "job"})
                if row["platform"] != platform or any(not isinstance(v, str) for v in row.values()):
                    raise ProtocolError("Invalid platform instance")
            return rows
        return self.api._queries.request("platform_list", dict(platform=platform), publish)

    def open(self, candidate, config, environment):
        return self.api.open_initial("platform_open", dict(session_id=uuid.uuid4().hex,
            platform="CDNS-IC", candidate=candidate, provider_config=config,
            environment=environment))
