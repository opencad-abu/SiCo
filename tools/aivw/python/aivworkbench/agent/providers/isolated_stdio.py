"""Passive provider using the qualified public-only namespace boundary."""

import json
from pathlib import Path

from ..backend import BaseProvider, ProviderResponse, ProviderUnavailable
from ..context import redact_secrets
from ..process_isolation import run_public_worker
from ..protocol import Action, ErrorCode, decode_json


class IsolatedStdioProvider(BaseProvider):
    name = "isolated_public_stdio"
    version = "1"

    def __init__(self, public_bundle, *, model_id, timeout=30.):
        super().__init__()
        self.public_bundle = Path(public_bundle)
        self.model_id = model_id
        self.timeout = timeout
        self.executions = []
        self.feedback_seen = []

    def next_action(self, request):
        self._check_interrupt()
        if request.previous_result:
            feedback = request.previous_result.get("result", {}).get("outputs", {}).get("gate_feedback")
            if feedback:
                self.feedback_seen.append(feedback["sha256"])
        payload = json.dumps(redact_secrets(request.to_dict()), sort_keys=True, allow_nan=False).encode()+b"\n"
        try:
            output, execution = run_public_worker(self.public_bundle, payload, timeout=self.timeout)
            value = decode_json(output.decode())
            action = value if isinstance(value, Action) else Action.from_dict(value)
        except (ValueError, OSError, TimeoutError) as exc:
            raise ProviderUnavailable(ErrorCode.MODEL_PROVIDER_UNAVAILABLE.value,
                                      "isolated stdio provider unavailable: %s" % exc) from exc
        self.executions.append(execution)
        return ProviderResponse.from_action(action, provider=self.name, model_id=self.model_id,
                                            raw_metadata={"isolation": execution["isolation"]})
