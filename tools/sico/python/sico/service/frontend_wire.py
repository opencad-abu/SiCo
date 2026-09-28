"""One bounded transfer slot on an authenticated service connection."""

import hashlib
import threading
from concurrent.futures import Future

from ..transport.framing import ProtocolError
from .frontend_assets import result_budget
from .frontend_codec import (
    CHUNK_BYTES,
    PAYLOAD_BYTES,
    decode_chunk,
    decode_payload,
    digest,
    encode_chunk,
    encode_payload,
)


class FrontendWire:
    def __init__(self, hub, identity, connection=None):
        from .session_control import ControlConnection

        self.hub, self.identity = hub, identity
        self.connection = connection or ControlConnection(identity)
        self.control = None
        self.closed = threading.Event()
        self.upload = bytearray()
        self.checksum = hashlib.sha256()
        self.result = None
        self.operation = self.method = None
        self.progress = ""

    def reply(self, method, params, operation_id, control=None):
        step = params["step"]
        if step == "reset":
            if self.result is not None and not self.result.done():
                raise ProtocolError("Cannot replace an active frontend operation")
            self.upload.clear()
            self.checksum = hashlib.sha256()
            self.result, self.operation, self.method = None, operation_id, method
            self.control = control
            self.progress = ""
            return {"state": "empty"}
        if ((operation_id, method) != (self.operation, self.method)
                or control != self.control):
            raise ProtocolError("Frontend transfer belongs to another operation")
        if step == "put":
            data = decode_chunk(params["data"])
            if (self.result is not None or params["offset"] != len(self.upload)
                    or len(self.upload) + len(data) > PAYLOAD_BYTES):
                raise ProtocolError("Frontend upload order or budget mismatch")
            self.upload.extend(data)
            self.checksum.update(data)
            return {"state": "upload", "size": len(self.upload)}
        if step == "run":
            if (self.result is not None or params["size"] != len(self.upload)
                    or self.checksum.hexdigest() != params["digest"]):
                raise ProtocolError("Frontend upload manifest mismatch")
            payload, self.upload = self.upload, bytearray()
            self.result = Future()
            self.hub.start(self, payload)
            return {"state": "pending", "instructions": self.progress}
        if self.result is None:
            raise ProtocolError("Frontend operation has not started")
        if not self.result.done():
            return {"state": "pending", "instructions": self.progress}
        data, checksum = self.result.result()
        offset = params["offset"]
        if offset >= len(data) or offset % CHUNK_BYTES:
            raise ProtocolError("Frontend result offset mismatch")
        return dict(state="ready", size=len(data), digest=checksum, offset=offset,
                    data=encode_chunk(data[offset:offset + CHUNK_BYTES]))

    def execute(self, payload):
        limit = PAYLOAD_BYTES
        try:
            request = decode_payload(payload)
            if self.method == "frontend.query":
                limit = result_budget(request.get("operation"))
            value = self.hub.execute(self, request)
            response = {"ok": True, "value": value}
        except ProtocolError:
            response = {"ok": False, "kind": "protocol", "message": "服务数据校验失败，请重新连接"}
        except ValueError as exc:
            response = {"ok": False, "kind": "rejected", "message": str(exc)[:512]}
        except Exception:
            response = {"ok": False, "kind": "unknown", "message": "结果未确认，请核对原请求"}
        try:
            data = encode_payload(response, limit=limit)
        except ProtocolError:
            data = encode_payload({"ok": False, "kind": "protocol",
                                   "message": "服务发布结果超过内容预算"})
        self.result.set_result((data, digest(data)))
        if self.closed.is_set():
            self.hub.retire(self)

    def close(self):
        self.connection.closed.set()
        self.closed.set()
        self.upload.clear()
        self.hub.retire(self)
