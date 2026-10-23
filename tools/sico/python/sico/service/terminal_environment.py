"""Restore captured user values only for an interactive terminal child.

The launcher owns pre-Python provenance. Missing legacy set markers mean the
saved value is authoritative; they cannot prove the original variable was unset.
No environment values are persisted or put in command arguments by this module.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from cadenv import restore_eda_temp_environment, restore_virtuoso_environment
from sicosessionenv import ENVIRONMENT_NAMES

LOGIN_SHELLS = frozenset({"bash", "sh", "dash", "ksh", "zsh"})
SHELL_ENV_DROP = frozenset({
    "LD_LIBRARY_PATH", "LD_PRELOAD", "LD_AUDIT", "PYTHONHOME", "PYTHONPATH",
    "PYTHONNOUSERSITE", "SICO_API_KEY", "CAD_AGENT_API_KEY", "CAD_CODEX_API_KEY",
    "SICO_CODEX_API_KEY", "SICO_MODEL_KEY", "SICO_MCP_TOKEN", "CAD_COPILOT_MODEL_KEY",
    "CAD_COPILOT_MCP_TOKEN", "CAD_STUDIO_MCP_TOKEN", *ENVIRONMENT_NAMES,
    "SICO_AI_CONTROL_FD", "CAD_AI_CONTROL_FD",
})


def _source(environment):
    source = dict(os.environ if environment is None else environment)
    if any(not isinstance(key, str) or not key or "=" in key or "\0" in key
           or not isinstance(value, str) or "\0" in value
           for key, value in source.items()):
        raise ValueError("Invalid terminal environment")
    return source


def shell_environment(environment=None):
    """Return a detached child environment, preserving captured empty/unset values."""
    source = _source(environment)
    result = restore_eda_temp_environment(source)
    for name in SHELL_ENV_DROP:
        result.pop(name, None)
    result = restore_virtuoso_environment(result)
    # These aliases carry credentials owned by the model runtime, not site settings.
    for name in tuple(result):
        if name.startswith(("SICO_RESOURCE_", "SICO_KNOWLEDGE_")):
            result.pop(name)
    return result


def shell_command(environment=None, *, command=None, inherited=None):
    """Build argv with removals only; restored values go through the child's env.

    QTermWidget merges setEnvironment with its parent. The env wrapper removes
    absent child variables after that merge without mutating the GUI environment.
    """
    source = _source(environment)
    target = shell_environment(source)
    if command is None:
        shell = source.get("SHELL", "").strip() or "/bin/bash"
        if not PurePosixPath(shell).is_absolute():
            raise ValueError("Terminal shell must have an absolute path")
        command = [shell, *(["-l"] if PurePosixPath(shell).name in LOGIN_SHELLS else [])]
    elif (not isinstance(command, (list, tuple)) or not command
          or any(not isinstance(arg, str) or "\0" in arg for arg in command)
          or not command[0]):
        raise ValueError("Invalid terminal command")
    parent = source if inherited is None else _source(inherited)
    removed = sorted((source.keys() | parent.keys()) - target.keys())
    if not removed:
        return list(command)
    options = [item for name in removed for item in ("-u", name)]
    return ["/usr/bin/env", *options, "--", *command]
