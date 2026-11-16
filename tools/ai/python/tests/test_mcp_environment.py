"""Environment launcher compatibility and consumption contracts."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from cadai import mcp, mcp_environment


@pytest.mark.parametrize("preferred", [True, False])
def test_current_environment_priority_and_retired_rejection(monkeypatch, tmp_path, preferred):
    import os
    observed = {}
    keys = {"RUNTIME": str(tmp_path), "SOCKET": str(tmp_path / "bridge.sock"),
            "SPOOL": str(tmp_path / "spool"), "TOKEN": "fixture", "WORKSPACE": str(tmp_path)}
    for key, value in keys.items():
        monkeypatch.setenv("CAD_CODEX_" + key, value)
        monkeypatch.delenv("SICO_AI_" + key, raising=False)
        if preferred:
            monkeypatch.setenv("SICO_AI_" + key, value)
            monkeypatch.setenv("CAD_CODEX_" + key, "unused-legacy")
    monkeypatch.setenv("SICO_AI_PROFILE", "verification")
    runtime = SimpleNamespace(socket=Path(keys["SOCKET"]))
    monkeypatch.setattr(mcp_environment.RuntimePaths, "from_root", lambda root, spool: (
        observed.update(root=root, spool=spool) or runtime
    ))

    class Client:
        def __init__(self, socket, token, timeout, runtime):
            observed.update(socket=socket, token=token, timeout=timeout)

        def connect(self):
            observed["connected"] = True

    def server(client, *, workspace, profile):
        observed.update(workspace=workspace, profile=profile)
        return SimpleNamespace(run=lambda: 17)

    monkeypatch.setattr(mcp_environment, "SocketClient", Client)
    monkeypatch.setattr(mcp, "McpServer", server)
    if not preferred:
        with pytest.raises(ValueError, match="removed"):
            mcp.run_mcp_from_environment(12.5)
        assert not observed
        return
    assert mcp.run_mcp_from_environment(12.5) == 17
    assert observed == dict(root=keys["RUNTIME"], spool=keys["SPOOL"],
                           socket=Path(keys["SOCKET"]), token="fixture", timeout=12.5,
                           connected=True, workspace=tmp_path.resolve(), profile="verification")
    for key in keys:
        assert "CAD_AI_" + key not in os.environ
        assert "CAD_CODEX_" + key not in os.environ
    assert "SICO_AI_PROFILE" not in os.environ


def test_mismatched_socket_is_rejected_before_connect(monkeypatch, tmp_path):
    for key, value in {"RUNTIME": str(tmp_path), "SOCKET": str(tmp_path / "other.sock"),
                       "TOKEN": "fixture"}.items():
        monkeypatch.setenv("SICO_AI_" + key, value)
    for key in ("SPOOL", "WORKSPACE"):
        monkeypatch.delenv("SICO_AI_" + key, raising=False)
        monkeypatch.delenv("CAD_CODEX_" + key, raising=False)
    monkeypatch.setattr(mcp_environment.RuntimePaths, "from_root", lambda *args: SimpleNamespace(
        socket=tmp_path / "bridge.sock",
    ))
    monkeypatch.setattr(mcp_environment, "SocketClient", lambda *args: pytest.fail("connected"))
    with pytest.raises(RuntimeError, match="outside the private runtime"):
        mcp.run_mcp_from_environment()
