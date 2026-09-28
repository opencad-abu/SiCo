"""One-use dispatch check on the existing authenticated desktop RPC connection."""

import time
import uuid
from concurrent.futures import CancelledError

from .methods import QueryUnavailable

CAPABILITY = "input_wait_gate_v1"
TIMEOUT = 5


def remote_gate(connection, closed):
    """Return a broker callback; never serialize Python callbacks or grants."""
    def check():
        nonce = uuid.uuid4().hex
        try:
            connection.send({"kind": CAPABILITY, "nonce": nonce})
            result = connection.receive(deadline=time.monotonic() + TIMEOUT,
                                        cancelled=closed.is_set)
        except (OSError, EOFError, ValueError, CancelledError):
            raise QueryUnavailable("waiting_user", "宿主状态未确认，未派发原生操作") from None
        if (result != {"kind": CAPABILITY, "nonce": nonce, "allowed": True}
                or result.get("allowed") is not True):
            raise QueryUnavailable("waiting_user", "用户问题待答复或来源已变化，未派发原生操作")
        return None
    return check


def answer_gate(connection, message, check):
    nonce = message.get("nonce")
    if not isinstance(nonce, str) or len(nonce) != 32:
        raise ValueError("Invalid input-wait check")
    allowed = False
    if check is not None:
        try:
            allowed = check() is None
        except Exception:
            pass  # Failure never grants permission and never includes question data.
    connection.send({"kind": CAPABILITY, "nonce": nonce, "allowed": allowed})
