"""Compiled Assistant MCP must execute its installed ELF, without source fallback."""

from pathlib import Path

import pytest

from cadai import mcp_command as owner
from sicopaths import IDENTITY_BYTES, MARKER


def test_source_mcp_uses_explicit_bootstrap_with_user_site_disabled():
    assert owner.mcp_command("/python", Path("/dev/sico-ai"), 30) == [
        "/python", "-s", "/dev/sico-ai", "mcp", "--request-timeout", "35"]


def test_native_mcp_requires_contained_executable_elf(tmp_path, monkeypatch):
    (tmp_path / MARKER).parent.mkdir(parents=True)
    (tmp_path / MARKER).write_bytes(IDENTITY_BYTES)
    (tmp_path / "bin").mkdir()
    (tmp_path / "tools/common").mkdir(parents=True)
    module = tmp_path / "tools/ai/python/cadai/mcp_command.cpython-39-x86_64-linux-gnu.so"
    monkeypatch.setattr(owner, "__file__", str(module))
    entry = tmp_path / "tools/ai/bin/aiassistant"
    entry.parent.mkdir(parents=True)
    environment = {"SICO_HOME": str(tmp_path)}
    with pytest.raises(ValueError, match="executable ELF"):
        owner.mcp_command("/python", "/missing/source", 30, environment=environment)
    entry.write_bytes(b"#!/bin/sh\n")
    entry.chmod(0o700)
    with pytest.raises(ValueError, match="executable ELF"):
        owner.mcp_command("/python", "/missing/source", 30, environment=environment)
    entry.write_bytes(b"\x7fELF")
    assert owner.mcp_command("/python", "/missing/source", 30, environment=environment) == [
        str(entry), "mcp", "--request-timeout", "35"]
    entry.unlink()
    entry.symlink_to("/bin/true")
    with pytest.raises(ValueError, match="escapes"):
        owner.mcp_command("/python", "/missing/source", 30, environment=environment)
