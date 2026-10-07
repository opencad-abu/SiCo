"""Launch the MCP server from the private runtime environment."""

import os
from pathlib import Path
from sicosessionenv import consume

from .agent_profile import INTERACTIVE_PROFILE, validate_profile
from .mcp_transport import SocketClient
from .runtime import RuntimePaths


def run_mcp_from_environment(timeout: float = 305.0) -> int:
    from .mcp import McpServer

    delegated = consume(os.environ)
    root, socket_path, spool, token, workspace_text = (
        delegated[name] for name in ("RUNTIME", "SOCKET", "SPOOL", "TOKEN", "WORKSPACE"))
    if not root or not socket_path or not token:
        raise RuntimeError(
            "SICO_AI_RUNTIME, SICO_AI_SOCKET, and SICO_AI_TOKEN are required"
        )
    runtime = RuntimePaths.from_root(root, spool)
    if Path(socket_path) != runtime.socket:
        raise RuntimeError("SICO_AI_SOCKET is outside the private runtime directory")
    profile = delegated["PROFILE"] if delegated["PROFILE"] is not None else INTERACTIVE_PROFILE
    validate_profile(profile)
    client = SocketClient(runtime.socket, token, timeout, runtime)
    client.connect()
    workspace = Path(workspace_text).expanduser().resolve() if workspace_text else None
    return McpServer(client, workspace=workspace, profile=profile).run()
