"""DataHub v1 Python contracts, codec and transport adapters."""

from .codec import ArtifactRef, artifact_ref
from .dh1 import Dh1Request
from .envelope import Envelope, Error, Meta, Page
from .endpoint import UnixEnvelopeClient, UnixEnvelopeServer
from .errors import ERROR_CODES, HubError, may_retry
from .registry import Contract, Registry, default_registry
from .site import probe_capabilities, probe_site

__version__ = "1.0.0"

__all__ = [
    "ArtifactRef",
    "Contract",
    "Dh1Request",
    "ERROR_CODES",
    "Envelope",
    "Error",
    "HubError",
    "Meta",
    "Page",
    "Registry",
    "UnixEnvelopeClient",
    "UnixEnvelopeServer",
    "artifact_ref",
    "default_registry",
    "may_retry",
    "probe_capabilities",
    "probe_site",
]
