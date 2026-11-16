"""Line-delimited JSON-RPC transport for layout backends."""

from __future__ import annotations

import json
import select
import subprocess
from pathlib import Path
from typing import Any


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class BackendProcessError(RuntimeError):
    pass


def encode_request(request_id: int, method: str, params: dict[str, Any]) -> bytes:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def decode_response(line: bytes, request_id: int) -> dict[str, Any]:
    try:
        value = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendProcessError("backend returned invalid JSON") from exc
    if (not isinstance(value, dict) or value.get("jsonrpc") != "2.0"
            or value.get("id") != request_id):
        raise BackendProcessError("backend returned an invalid JSON-RPC response")
    error = value.get("error")
    if error is not None:
        if not isinstance(error, dict):
            raise BackendProcessError("backend returned an invalid JSON-RPC error")
        raise RpcError(int(error.get("code", -32603)), str(error.get("message", "backend error")),
                       error.get("data"))
    result = value.get("result")
    if not isinstance(result, dict):
        raise BackendProcessError("backend result must be an object")
    return result


class JsonRpcProcess:
    """One backend process and one JSON-RPC stream."""

    def __init__(self, command: list[str], *, cwd: Path, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self._next_id = 0
        self.process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self.process.stdin is None or self.process.stdout is None:
            raise BackendProcessError("backend pipes are unavailable")
        self._next_id += 1
        request_id = self._next_id
        self.process.stdin.write(encode_request(request_id, method, params))
        self.process.stdin.flush()
        wait = self.timeout if timeout is None else timeout
        ready, _, _ = select.select([self.process.stdout], [], [], wait)
        if not ready:
            self.close()
            raise TimeoutError(f"backend did not answer {method}")
        line = self.process.stdout.readline()
        if not line:
            diagnostic = self._diagnostic()
            self.close()
            raise BackendProcessError(f"backend closed before {method}: {diagnostic}")
        return decode_response(line, request_id)

    def _diagnostic(self) -> str:
        if self.process.stderr is None:
            return ""
        try:
            return self.process.stderr.read().decode("utf-8", errors="replace")[-4000:]
        except OSError:
            return ""

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                if self.process.stdin is not None:
                    self.process.stdin.close()
                self.process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                try:
                    self.process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=1.0)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()

    def __enter__(self) -> JsonRpcProcess:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
