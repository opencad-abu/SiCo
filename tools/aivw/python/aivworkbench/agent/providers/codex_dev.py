"""Development-only Codex comparison boundary.

No subprocess fallback is provided here.  Production code must use an AIVW
provider contract; developers may subclass this stub or inject a recorded
adapter in tests.
"""

from __future__ import annotations

from ..backend import BaseProvider, ProviderRequest, ProviderResponse, ProviderUnavailable
from ..protocol import ErrorCode


class CodexDevProvider(BaseProvider):
    name = "codex_dev"
    version = "dev"

    def next_action(self, request: ProviderRequest) -> ProviderResponse:
        raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value, "Codex development adapter is not installed in the production bundle")


__all__ = ["CodexDevProvider"]
