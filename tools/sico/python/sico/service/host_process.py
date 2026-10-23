"""SKILL Ask worker: bounded private input, durable ack and independent wake notice."""

import sys

from ..core.contracts import BoundContext, identifier
from ..transport.framing import ProtocolError
from ..transport.quick_codec import decode_submission
from .diagnostics import diagnostics
from .frontend_ask import FrontendAsk
from .service_errors import RequestUnsent

MAX_LINE = 262144


def run_host_submit(args):
    input_id = None
    frontend = result = None
    try:
        raw = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not raw or len(raw) > MAX_LINE or not raw.endswith(b"\n"):
            raise ProtocolError("Invalid host input line")
        message = decode_submission(raw)
        input_id = identifier(message["id"])
        context = BoundContext.from_record(message["context"])
        frontend = FrontendAsk(args.launch_dir, context, provider_config=args.provider_config)
        options = {"ask_sequence": message["ask_sequence"]} if "ask_sequence" in message else {}
        result = (frontend.submit(message["text"], input_id, **options) if args.command == "host-submit"
                  else frontend.query(input_id))
        kind = result.outcome
    except ProtocolError:
        kind = "unknown"
    except (ValueError, RequestUnsent):
        kind = "rejected" if args.command == "host-submit" else "unknown"
    except Exception:
        kind = "unknown"
    finally:
        if frontend is not None:
            try:
                frontend.close()
            except Exception as exc:
                diagnostics.submit(__name__, "Ask client cleanup failed", exc)
    if result is not None and kind == "accepted":
        # Wake is advisory; a formatting/write failure cannot change the durable receipt.
        try:
            suffix = ""
            if result.ask_sequence is not None:
                head = result.queue_head
                suffix = " " + str(result.ask_sequence) + " " + (head["input_id"] if head else input_id)
            print("@wake " + input_id + " " + result.address.session.session_id + " "
                  + result.address.service_id + " " + result.address.session.runtime_id + suffix,
                  flush=True)
        except Exception as exc:
            diagnostics.submit(__name__, "Ask wake notice failed", exc)
    if input_id is not None:
        reason = " " + result.code if result is not None and kind == "rejected" else ""
        print("@" + kind + " " + input_id + reason, flush=True)
    return 0 if result is not None else 2
