"""Controller transport double and private runtime layout for source tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cadai.runtime import RuntimePaths


class FakeTransport:
    def __init__(self, result: dict[str, Any] | None = None, *, timeout: bool = False):
        self.result = result or {"ok": True, "value": "3", "output": ""}
        self.timeout = timeout
        self.requests: list[dict[str, Any]] = []
        self.sources: list[bytes] = []

    def request(self, payload: dict[str, Any], timeout: float):
        self.requests.append(dict(payload))
        if source_path := payload.get("source_path"):
            self.sources.append(Path(source_path).read_bytes())
        if self.timeout:
            raise TimeoutError("execution status is unknown and was not retried")
        Path(payload["output_path"]).write_text(
            json.dumps(self.result, ensure_ascii=False), encoding="utf-8"
        )
        return True, {"result_path": payload["output_path"]}


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimePaths:
    root = tmp_path / "node-local" / "cad-ai-test"
    spool = root / "spool"
    bridge = (
        tmp_path
        / "shared"
        / ".cad"
        / "ai"
        / f".cad-ai-runtime-{os.getuid()}"
        / "session-test"
    )
    spool.mkdir(parents=True, mode=0o700)
    bridge.mkdir(parents=True, mode=0o700)
    root.chmod(0o700)
    spool.chmod(0o700)
    bridge.parent.chmod(0o700)
    bridge.chmod(0o700)
    return RuntimePaths(root, root / "bridge.sock", spool, bridge_spool=bridge)
