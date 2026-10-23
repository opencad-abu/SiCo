"""Prepare the isolated child environment for a managed terminal session."""

from __future__ import annotations

import os
from pathlib import Path

from sicoenv import RETIRED_NAMES, promote, read
from sicoenv import value as sico_environment_value
from sicosessionenv import clear as clear_session
from sicosessionenv import publish as publish_session
from sicotemp import initialize as initialize_temporary

from .agent_profile import INTERACTIVE_PROFILE, validate_profile
from .installation import current
from .lsf import capture_virtuoso_environment
from .optional_resources import ENVIRONMENT_KEYS as OPTIONAL_RESOURCE_KEYS
from .optional_resources import PATH_NAMES, apply_data_sources
from .optional_resources import prepare as prepare_optional_resources
from .runtime import RuntimePaths
from .search_runtime import prepare_search_environment
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
        "SICO_PDK_WORKFLOW", "SICO_BACKEND", "SICO_PROVIDER",
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
    apply_pdk_workflow(env, workspace)
    env["SICO_BACKEND"], env["SICO_PROVIDER"] = model_settings(workspace, env)
    promote(env, *(name for name in RETIRED_NAMES if name not in PATH_NAMES))
    apply_data_sources(env, workspace)
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
    if not python_libraries and "SICO_AI_PYTHON_LIBRARY_PATH" not in env:
        python_libraries = sico_environment_value(
            env, "SICO_SYSTEM_LD_LIBRARY_PATH", ("CAD_SYSTEM_LD_LIBRARY_PATH",), ""
        ).strip()
    if not python_libraries and "SICO_AI_PYTHON_LIBRARY_PATH" not in env:
        python_libraries = current(env).runtime_library_path or ""
    env["LD_LIBRARY_PATH"] = python_libraries
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


def pdk_workflow(workspace, environment):
    """Only an explicit headless/CI workflow pins the process."""
    configured = str(environment.get("SICO_PDK_WORKFLOW", "")).strip()
    return configured if configured in {"design", "generation"} else None


def apply_pdk_workflow(environment, workspace):
    """Apply the optional workflow override without inventing a default mode."""
    workflow = pdk_workflow(workspace, environment)
    if workflow:
        environment["SICO_PDK_WORKFLOW"] = workflow
    else:
        environment.pop("SICO_PDK_WORKFLOW", None)


def model_settings(workspace, environment):
    """Project model settings win for new sessions; explicit env is the fallback."""
    try:
        from sico.agent_resources.model import read as read_model

        document = read_model(workspace)
        if document.get("revision") is not None:
            return document["backend"], document["provider"]
    except (ValueError, OSError, RuntimeError):
        pass
    backend = str(environment.get("SICO_BACKEND", "")).strip() or "codex"
    if backend not in {"codex", "python"}:
        backend = "codex"
    provider = str(environment.get("SICO_PROVIDER", "")).strip()
    allowed = {"codex": {"responses"}, "python": {"anthropic", "openai"}}[backend]
    if provider not in allowed:
        provider = "responses" if backend == "codex" else "anthropic"
    return backend, provider


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
