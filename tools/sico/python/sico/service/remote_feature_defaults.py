"""Project-level feature defaults, separate from session-scoped settings."""

from copy import deepcopy

from ..transport.framing import ProtocolError
from .service_protocol import exact_fields


class RemoteFeatureDefaults:
    def __init__(self, api):
        self._api = api

    def read(self):
        def publish(value):
            exact_fields(value, {"groups", "overrides"})
            if not isinstance(value["groups"], list) or not isinstance(value["overrides"], dict):
                raise ProtocolError("Invalid feature defaults result")
            return value
        return self._api._queries.request("feature_defaults", {}, publish)

    def save(self, groups):
        def publish(value):
            exact_fields(value, {"groups", "overrides"})
            if not isinstance(value["groups"], list) or not isinstance(value["overrides"], dict):
                raise ProtocolError("Invalid feature defaults result")
            return value
        return self._api._commands.request(
            "feature_defaults_save", {"groups": deepcopy(groups)}, publish)
