"""SKILL Ask worker: bounded private input, durable ack and independent wake notice."""

import os
import sys

from ..core.contracts import BoundContext, identifier
from ..transport.framing import ProtocolError
from ..transport.quick_codec import decode_submission
from .host_gateway import HostServiceGateway
from .service_errors import RequestUnsent

MAX_LINE = 262144


def run_host_submit(args):
    input_id = None
    gateway = None
    try:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not raw or len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            raise ProtocolError("Invalid host input line")
        message = decode_submission(raw)
        input_id = identifier(message["id"])
        context = BoundContext.from_record(message["context"])
        if not os.path.samefile(args.launch_dir, context.snapshot.get("cwd", "")):
            raise ValueError("Host project mismatch")
        gateway = HostServiceGateway(args.launch_dir, context,
                                     provider_config=args.provider_config)
        result = (gateway.submit(message) if args.command == "host-submit"
                  else gateway.query(input_id))
        kind = result.outcome
        if kind == "accepted":
            # GUI activation is advisory. It cannot turn durable acceptance into failure.
            print("@wake " + input_id + " " + result.address.session.session_id + " "
                  + result.address.service_id + " " + result.address.session.runtime_id, flush=True)
        print("@" + kind + " " + input_id, flush=True)
        return 0
    except ProtocolError:
        kind = "unknown"
    except (ValueError, RequestUnsent):
        kind = "rejected"
    except Exception:
        kind = "unknown"
    finally:
        if gateway is not None:
            gateway.close()
    if input_id is not None:
        print("@" + kind + " " + input_id, flush=True)
    return 2
