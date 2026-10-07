"""Claude terminal command and its stdio MCP configuration."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

from sicoenv import RETIRED_NAMES, promote, read as environment_setting

from cadenv import EDA_TEMP_MARKERS, preserve_eda_temp_environment
from sicostate import export_environment
from sicotemp import selected_state
from sicosessionenv import publish as publish_session, value as session_value

from .agent_profile import INTERACTIVE_PROFILE, allowed_tool_names, validate_profile
from .launch_policy import MCP_TOOL_NAMES as _ALL_MCP_TOOLS
from .launch_policy import assistant_instructions
from .runtime import RuntimePaths
from .mcp_command import mcp_command
from .optional_resources import PATH_NAMES, forwarded as optional_resources
from .tool_help_schema import DOC_ENV_NAMES


def build_claude_mcp_config(
    python: str,
    mcp_entry: Path,
    runtime: RuntimePaths,
    token: str,
    timeout: float,
    environment: Mapping[str, str] | None = None,
    profile: str = INTERACTIVE_PROFILE,
) -> bytes:
    source = dict(os.environ if environment is None else environment)
    promote(source, *(name for name in RETIRED_NAMES if name not in PATH_NAMES))
    validate_profile(profile)
    server_environment = {"PYTHONDONTWRITEBYTECODE": "1"}
    publish_session(server_environment, RUNTIME=runtime.root, SOCKET=runtime.socket,
                    SPOOL=runtime.spool, TOKEN=token)
    if profile != INTERACTIVE_PROFILE:
        server_environment["SICO_AI_PROFILE"] = profile
    from .installation import current
    server_environment["SICO_HOME"] = str(current(source).root)
    for name in (*DOC_ENV_NAMES, "PATH"):
        if source.get(name):
            server_environment[name] = source[name]
    if "SICO_TEMP_DIR" in source or "CAD_TEMP_DIR" in source:
        state = selected_state(source)
        export_environment(source, state)
        preserve_eda_temp_environment(source)
        export_environment(server_environment, state)
        rendered_temp = str(state / "ai")
        for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
            server_environment[name] = rendered_temp
        server_environment["XDG_CACHE_HOME"] = str(Path(rendered_temp) / "cache")
        server_environment["XDG_RUNTIME_DIR"] = str(Path(rendered_temp) / "runtime")
    for name in EDA_TEMP_MARKERS:
        if name in source:
            server_environment[name] = source[name]
    python_libraries = environment_setting(source, "SICO_AI_PYTHON_LIBRARY_PATH", "").strip()
    if python_libraries or "SICO_AI_PYTHON_LIBRARY_PATH" in source:
        server_environment["LD_LIBRARY_PATH"] = python_libraries
    workspace = session_value(source, "WORKSPACE", "").strip()
    server_environment.update(optional_resources(source))
    if workspace:
        workspace_path = Path(workspace).expanduser()
        if not workspace_path.is_absolute():
            raise ValueError("SICO_AI_WORKSPACE must be an absolute path")
        server_environment["SICO_AI_WORKSPACE"] = str(workspace_path.resolve())
    for name in ("SICO_PYTHON_ROOT", "SICO_PYTHON"):
        python_value = source.get(name, "").strip()
        if python_value:
            server_environment[name] = python_value
    command = mcp_command(python, mcp_entry, timeout, environment=source)
    config = {
        "mcpServers": {
            "virtuoso": {
                "type": "stdio",
                "command": command[0],
                "args": command[1:],
                "env": server_environment,
                "timeout": math.ceil(timeout + 10) * 1000,
            }
        }
    }
    return json.dumps(config, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_claude_command(
    claude: str,
    mcp_config: Path,
    environment: Mapping[str, str] | None = None,
    profile: str = INTERACTIVE_PROFILE,
) -> list[str]:
    source = os.environ if environment is None else environment
    validate_profile(profile)
    allowed = allowed_tool_names(profile, _ALL_MCP_TOOLS)
    command = [
        claude,
        "--mcp-config",
        str(mcp_config),
        "--strict-mcp-config",
        "--permission-mode",
        "default",
        "--no-chrome",
        f"--allowedTools={','.join(f'mcp__virtuoso__{tool}' for tool in allowed)}",
        "--name",
        "Virtuoso",
        "--append-system-prompt",
        assistant_instructions(profile),
    ]
    model = environment_setting(source, "SICO_CLAUDE_MODEL", "").strip()
    if model:
        command.extend(["--model", model])
    return command
