"""Stable provenance boundary between bundled Codex CLI and AIVW runs.

This module deliberately does not depend on Codex's private app-server wire
protocol.  The controller can use it with the release CLI today and enrich it
with turn events later without changing the AIVW evidence contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from typing import Any, Mapping

ADAPTER_VERSION = "aivw-codex-adapter-v1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+@=-]{0,159}$")


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a bounded identifier")
    return value


def legacy_canonical_digest(value: Any) -> str:
    """Compute the historical Codex provenance digest.

    Existing provenance records may contain Python's ``NaN``/``Infinity``
    spellings, so this byte contract remains available for replay and
    verification.  New callers that control input validation should use
    :func:`strict_canonical_digest`.
    """
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def strict_canonical_digest(value: Any) -> str:
    """Compute a UTF-8 canonical digest while rejecting non-finite floats."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Private compatibility name retained for existing callers during migration.
_canonical_digest = legacy_canonical_digest


def binary_sha256(executable: str | os.PathLike[str]) -> str:
    """Hash the resolved CLI without following a mutable symlink twice."""
    path = Path(executable).expanduser().resolve(strict=True)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"Codex executable is not an executable file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cli_version(executable: str, *, timeout: float = 3.0) -> str:
    """Read a bounded version string; unknown output is recorded explicitly."""
    # Test/probe overrides such as /bin/true are not a Codex release.  Avoid
    # launching an extra child for them: controller lifecycle tests replace
    # the terminal Popen boundary and the adapter must remain side-effect free.
    if Path(executable).name != "codex":
        return "unknown"
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    line = completed.stdout.strip().splitlines()[0] if completed.stdout.strip() else ""
    return line[:160] if line else "unknown"


@dataclass
class CodexCliAdapter:
    """Own the bounded lifecycle/provenance record for one Codex session."""

    executable: str
    profile: str
    run_id: str = field(default_factory=lambda: "aivw-" + uuid.uuid4().hex)
    source_generation: str | None = None
    provenance_path: str | os.PathLike[str] | None = None
    adapter_version: str = ADAPTER_VERSION
    session_id: str = field(default_factory=lambda: "codex-" + uuid.uuid4().hex)
    _turn_ids: list[str] = field(default_factory=list, init=False, repr=False)
    _response_digests: list[str] = field(default_factory=list, init=False, repr=False)
    _binary_sha256: str = field(init=False, repr=False)
    _cli_version: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.executable = str(Path(self.executable).expanduser().resolve(strict=True))
        _identifier(self.profile, "profile")
        _identifier(self.run_id, "run_id")
        _identifier(self.session_id, "session_id")
        if self.source_generation is not None:
            if not isinstance(self.source_generation, str) or not re.fullmatch(
                r"[0-9a-f]{64}", self.source_generation
            ):
                raise ValueError("source_generation must be a lowercase SHA-256 digest")
        if self.provenance_path is not None:
            path = Path(self.provenance_path).expanduser()
            if not path.is_absolute() or path.exists() and path.is_symlink():
                raise ValueError("provenance_path must be an absolute non-symlink path")
            self.provenance_path = str(path)
        self._binary_sha256 = binary_sha256(self.executable)
        self._cli_version = cli_version(self.executable)

    @property
    def binary_sha256(self) -> str:
        return self._binary_sha256

    @property
    def cli_version(self) -> str:
        return self._cli_version

    def record_turn(self, turn_id: str, response: Any) -> str:
        """Record only a digest of a bounded response, never conversation text."""
        _identifier(turn_id, "turn_id")
        if turn_id in self._turn_ids:
            raise ValueError(f"duplicate Codex turn: {turn_id}")
        digest = legacy_canonical_digest(response)
        self._turn_ids.append(turn_id)
        self._response_digests.append(digest)
        return digest

    def snapshot(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "host": "codex_cli",
            "provider": "bundled_codex_cli",
            "profile": self.profile,
            "cli_version": self.cli_version,
            "binary_sha256": self.binary_sha256,
            "adapter_version": self.adapter_version,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "turn_ids": list(self._turn_ids),
            "response_digests": list(self._response_digests),
        }
        if self.source_generation is not None:
            value["source_generation"] = self.source_generation
        return value

    def finish(self, *, exit_code: int, status: str) -> dict[str, Any]:
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError("exit_code must be an integer")
        _identifier(status, "status")
        value = self.snapshot()
        value.update({"exit_code": exit_code, "terminal_status": status})
        value["provenance_digest"] = legacy_canonical_digest(value)
        self._write(value)
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        if self.provenance_path is None:
            return
        path = Path(self.provenance_path)
        parent = path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise ValueError("provenance_path parent must be an existing non-symlink directory")
        temporary = parent / ("." + path.name + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)


__all__ = [
    "ADAPTER_VERSION",
    "CodexCliAdapter",
    "binary_sha256",
    "cli_version",
    "legacy_canonical_digest",
    "strict_canonical_digest",
]
