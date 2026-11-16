from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cadai.codex_adapter import (
    CodexCliAdapter,
    binary_sha256,
    legacy_canonical_digest,
    strict_canonical_digest,
)


def _fake_codex(tmp_path: Path) -> Path:
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nprintf 'codex 0.153.4\\n'\n", encoding="ascii")
    executable.chmod(0o700)
    return executable


def test_adapter_binds_release_identity_and_turn_digests(tmp_path: Path) -> None:
    executable = _fake_codex(tmp_path)
    adapter = CodexCliAdapter(
        str(executable),
        "verification",
        run_id="run-1",
        session_id="session-1",
        source_generation="a" * 64,
    )
    assert adapter.cli_version == "codex 0.153.4"
    assert adapter.binary_sha256 == hashlib.sha256(executable.read_bytes()).hexdigest()
    digest = adapter.record_turn("turn-1", {"action": "PROPOSE_MODEL", "value": 1})
    assert len(digest) == 64
    snapshot = adapter.snapshot()
    assert snapshot["host"] == "codex_cli"
    assert snapshot["provider"] == "bundled_codex_cli"
    assert snapshot["turn_ids"] == ["turn-1"]
    assert snapshot["response_digests"] == [digest]
    finished = adapter.finish(exit_code=0, status="completed")
    assert finished["terminal_status"] == "completed"
    assert len(finished["provenance_digest"]) == 64


def test_codex_digest_contract_keeps_legacy_records_and_strict_new_entrypoint() -> None:
    value = {"中文": "值", "z": 1.5, "a": [True, None]}
    expected = "c1c65a48fc50e698e10ec7a7dc1bc2a6c9b7d17f0dce8c3c9b37afb77a3fef82"
    assert legacy_canonical_digest(value) == expected
    assert strict_canonical_digest(value) == expected
    assert legacy_canonical_digest({"a": [True, None], "z": 1.5, "中文": "值"}) == expected
    assert legacy_canonical_digest(float("nan")) == "d5b592c05dc25b5032553f1b27f4139be95e881f73db33b02b05ab20c3f9981e"
    with pytest.raises(ValueError, match="not JSON compliant"):
        strict_canonical_digest(float("nan"))


def test_adapter_rejects_duplicate_turn_and_invalid_identity(tmp_path: Path) -> None:
    executable = _fake_codex(tmp_path)
    adapter = CodexCliAdapter(str(executable), "verification", run_id="run-1")
    adapter.record_turn("turn-1", {"ok": True})
    with pytest.raises(ValueError, match="duplicate"):
        adapter.record_turn("turn-1", {"ok": True})
    with pytest.raises(ValueError, match="bounded identifier"):
        adapter.record_turn("turn with spaces", {})


def test_binary_sha256_requires_executable_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="executable file"):
        binary_sha256(tmp_path)


def test_adapter_persists_only_to_explicit_aivw_path(tmp_path: Path) -> None:
    executable = _fake_codex(tmp_path)
    output = tmp_path / "run" / "codex-provenance.json"
    output.parent.mkdir()
    adapter = CodexCliAdapter(
        str(executable), "verification", run_id="run-1", provenance_path=output
    )
    adapter.finish(exit_code=0, status="completed")
    assert output.is_file()
    assert "codex_cli" in output.read_text(encoding="utf-8")
