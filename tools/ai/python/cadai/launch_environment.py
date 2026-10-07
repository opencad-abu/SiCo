"""Prepare the isolated child environment for a managed terminal session."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from sicotemp import initialize as initialize_temporary
from sicoenv import RETIRED_NAMES, promote, read, value as sico_environment_value
from sicosessionenv import clear as clear_session, publish as publish_session

from .agent_profile import INTERACTIVE_PROFILE, validate_profile
from .lsf import capture_virtuoso_environment
from .runtime import RuntimePaths
from .search_runtime import prepare_search_environment
from .optional_resources import (ENVIRONMENT_KEYS as OPTIONAL_RESOURCE_KEYS, PATH_NAMES,
                                 prepare as prepare_optional_resources)
from .terminal_environment import prepare_input_environment

_TERMINAL_BOOTSTRAP_ENV = frozenset(
    {
        "CAD_AI_OS_RELEASE_FILE",
        "CAD_AI_PLATFORM",
        "CAD_AI_PYQT_ROOT",
        "CAD_AI_PYTHON_LIBRARY_PATH",
        "CAD_AI_QT_PLUGIN_PATH",
        "SICO_AI_QT_PLUGIN_PATH",
        "SICO_AI_PYTHON_LIBRARY_PATH",
        "SICO_AI_OS_RELEASE_FILE", "SICO_AI_PLATFORM", "SICO_AI_PYQT_ROOT",
    }
)


def prepare_terminal_environment(
    runtime: RuntimePaths,
    token: str,
    workspace: Path,
    codex_home: Path | None,
    agent: str = "codex",
    *,
    temp_root: Path | None = None,
    profile: str = INTERACTIVE_PROFILE,
) -> tuple[dict[str, str], tuple[str, ...]]:
    if agent not in {"codex", "claude"}:
        raise ValueError(f"unsupported AI agent: {agent}")
    validate_profile(profile)
    env = dict(os.environ)
    promote(env, *(name for name in RETIRED_NAMES if name not in PATH_NAMES))
    capture_virtuoso_environment(env)
    prepare_search_environment(env)
    resource_warnings = prepare_optional_resources(env, workspace)
    exact = {
        "PYTHONHOME",
        "PYTHONPATH",
        "LD_PRELOAD",
        "LD_AUDIT",
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_SELECT",
        "QT_DIR",
        "QML2_IMPORT_PATH",
    }
    for key in list(env):
        if key in exact or key.startswith("QTDIR"):
            env.pop(key, None)
    # This terminal is entirely raster based. Avoid probing the remote X11
    # display for unusable Mesa/NVIDIA GLX integrations.
    env["QT_XCB_GL_INTEGRATION"] = "none"
    warnings = (*prepare_input_environment(env), *resource_warnings)
    python_libraries = read(env, "SICO_AI_PYTHON_LIBRARY_PATH", "").strip()
    env["LD_LIBRARY_PATH"] = (python_libraries if python_libraries or
        "SICO_AI_PYTHON_LIBRARY_PATH" in env else sico_environment_value(
            env, "SICO_SYSTEM_LD_LIBRARY_PATH", ("CAD_SYSTEM_LD_LIBRARY_PATH",), "").strip())
    initialize_temporary(env, cwd=workspace, temporary=temp_root)
    qt_plugin_path = read(env, "SICO_AI_QT_PLUGIN_PATH", "").strip()
    if qt_plugin_path:
        env["QT_PLUGIN_PATH"] = qt_plugin_path
    if agent == "codex":
        for key in list(env):
            if (
                key.startswith(("SICO_CLAUDE_", "CAD_CLAUDE_"))
                or key.startswith("ANTHROPIC_")
                or key.startswith("CLAUDE_CODE_")
            ):
                env.pop(key, None)
        publish_session(env, RUNTIME=runtime.root, SOCKET=runtime.socket, SPOOL=runtime.spool,
                        TOKEN=token, WORKSPACE=workspace, PROFILE=profile)
        if codex_home is not None:
            env["CODEX_HOME"] = str(codex_home)
    else:
        clear_session(env)
        for key in list(env):
            if (
                (key.startswith(("SICO_AI_", "CAD_AI_"))
                 and key not in _TERMINAL_BOOTSTRAP_ENV and key not in OPTIONAL_RESOURCE_KEYS)
                or key.startswith(("SICO_CODEX_", "CAD_CODEX_"))
                or key.startswith("CODEX_")
                or key.startswith("OPENAI_")
            ):
                env.pop(key, None)
        env.setdefault("CLAUDE_CODE_NO_FLICKER", "1")
        env.setdefault("MCP_TIMEOUT", "10000")
    return env, warnings


def sanitized_terminal_environment(
    runtime: RuntimePaths,
    token: str,
    workspace: Path,
    codex_home: Path | None,
    agent: str = "codex",
    *,
    temp_root: Path | None = None,
) -> dict[str, str]:
    environment, _warnings = prepare_terminal_environment(
        runtime, token, workspace, codex_home, agent, temp_root=temp_root
    )
    return environment
