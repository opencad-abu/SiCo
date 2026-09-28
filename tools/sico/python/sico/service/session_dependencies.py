"""Immutable dependency inputs captured for one service-owned session."""

from dataclasses import dataclass

from ..core.contracts import json_copy
from .published import freeze


@dataclass(frozen=True)
class SessionDependencies:
    """Process-memory inputs; never write to journals, discovery or diagnostics.

    The environment may contain credentials required to construct the provider.
    """

    provider_config: object
    environment: object
    bridge_descriptor: object
    background: object = None

    def __post_init__(self):
        if self.provider_config is not None and not isinstance(self.provider_config, dict):
            raise TypeError("Session provider configuration requires a mapping or None")
        if not isinstance(self.environment, dict) or not isinstance(self.bridge_descriptor, dict):
            raise TypeError("Session dependencies require mappings")
        object.__setattr__(self, "background", freeze(json_copy(self.background)))
        object.__setattr__(self, "provider_config",
                           freeze(None if self.provider_config is None
                                  else json_copy(self.provider_config)))
        object.__setattr__(self, "environment", freeze(json_copy(self.environment)))
        object.__setattr__(self, "bridge_descriptor", freeze(json_copy(self.bridge_descriptor)))
