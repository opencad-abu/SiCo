"""Read exact bounded UTF-8 CDF evidence in chunks via the existing native bridge."""

import json
import uuid

from .circuit_create import skill_literal
from .circuit_spec_schema import CircuitSpecError
from .skill_result import call_skill
from .skill_diagnostics import carry_output, output_fields, record_output


def transfer(client, code, *, capture=False):
    token = skill_literal("cdf-transfer:" + uuid.uuid4().hex)

    started = False
    try:
        ok, reply = call_skill(client, "aiCdfPut(" + code + " " + token + ")", native=not capture)
        if not ok:
            return False, reply
        started = True
        output = output_fields(reply)
        size = reply.get("bytes")
        if type(size) is not int or not 0 < size <= 2097152:
            raise CircuitSpecError("invalid CDF transfer size")
        chunks, offset = [], 0
        while offset < size:
            ok, part = call_skill(client, f"aiCdfRead({token} {offset})", native=True)
            if not ok:
                return False, carry_output(part, output)
            encoded = part.get("hex")
            if not isinstance(encoded, str) or not 0 < len(encoded) <= 12000:
                raise CircuitSpecError("invalid CDF transfer chunk")
            try:
                chunk = bytes.fromhex(encoded)
            except ValueError as exc:
                raise CircuitSpecError("invalid CDF hex data") from exc
            if len(chunk) != min(6000, size - offset):
                raise CircuitSpecError("CDF transfer length differs")
            chunks.append(chunk)
            offset += len(chunk)
        try:
            result = json.loads(b"".join(chunks).decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise CircuitSpecError("CDF result is not UTF-8 JSON") from exc
        if not isinstance(result, dict):
            raise CircuitSpecError("CDF result is not an object")
        result = carry_output(result, output)
        if not capture:
            record_output(result)
        return bool(result.get("ok")), result
    finally:
        if started:
            # Read/drop only; never retry an uncertain callback execution.
            call_skill(client, f"aiCdfDrop({token})", native=True)
