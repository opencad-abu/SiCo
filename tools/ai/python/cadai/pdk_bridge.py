"""Chunked read-only captures through the existing current-session client."""
from __future__ import annotations

import json
import uuid

from .skill_result import call_skill
from .pdk_schema import MAX_CAPTURE_BYTES, PdkUnavailable, skill_string


# Discovery operations implemented by aiPdkCapture (see tools/ai/docs/PDK_COLLECTION_SPEC_V1.md).
OPERATIONS = frozenset({"libraries", "directory", "device", "categories"})


class PdkBridge:
    def __init__(self, client):
        self.client = client

    def call(self, expression):
        ok, detail = call_skill(self.client, expression)
        if not ok:
            raise PdkUnavailable(detail.get("code", "bridge_unavailable"),
                                 detail.get("message", "Discovery capture failed"))
        return detail

    def capture(self, operation, target):
        if operation not in OPERATIONS:
            raise PdkUnavailable("unsupported_operation",
                                 "Unsupported discovery operation: " + str(operation))
        if not isinstance(target, dict):
            raise PdkUnavailable("invalid_capture", "Discovery target must be an object")
        # Only the category registry needs an explicit library scope up front; the other
        # operations keep their historical target validation (the SKILL side rejects the rest).
        if operation == "categories" and not isinstance(target.get("library"), str):
            raise PdkUnavailable("invalid_capture", "Category capture requires a library")
        token = "transfer:" + uuid.uuid4().hex
        # The SKILL process keeps only its first proposal. A replacement Virtuoso
        # must get a fresh identity even if this Python bridge object survives.
        proposal = "session:" + uuid.uuid4().hex
        values = (token, proposal, operation, target.get("library"),
                  target.get("cell"), target.get("view", "symbol"))
        if operation == "directory":
            values += (target.get("offset", 0), 32)
        captured = False
        try:
            result = self.call("aiPdkCapture(" + " ".join(map(skill_string, values)) + ")")
            captured = True
            size = result.get("characters")
            if type(size) is not int or not 0 < size <= MAX_CAPTURE_BYTES:
                raise PdkUnavailable("invalid_capture", "Invalid discovery transfer length")
            chunks, count = [], 0
            while count < size:
                encoded = self.call(f"aiPdkRead({skill_string(token)} {count})").get("hex")
                if not isinstance(encoded, str) or not encoded or len(encoded) > 12000:
                    raise PdkUnavailable("invalid_capture", "Invalid discovery transfer chunk")
                try:
                    part = bytes.fromhex(encoded)
                except ValueError as exc:
                    raise PdkUnavailable("invalid_capture", "Invalid hex transfer chunk") from exc
                if not part:
                    raise PdkUnavailable("invalid_capture", "Empty discovery transfer chunk")
                chunks.append(part)
                count += len(part)
                if count > size or count > MAX_CAPTURE_BYTES:
                    raise PdkUnavailable("capture_limit", "Discovery transfer exceeds limit")
            try:
                value = json.loads(b"".join(chunks).decode("utf-8"))
            except (ValueError, UnicodeError) as exc:
                raise PdkUnavailable("invalid_capture", "Discovery transfer is not JSON in UTF-8") from exc
            if not isinstance(value, dict) or not isinstance(value.get("context"), dict):
                raise PdkUnavailable("invalid_capture", "Discovery context missing")
            return value
        finally:
            # Release on decode/size/read failures too. Never retry an ambiguous capture.
            if captured:
                self.call(f"aiPdkDrop({skill_string(token)})")
