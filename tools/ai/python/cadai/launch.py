"""Compatibility exports for managed AI session launch helpers.

Each helper has one responsibility owner. Keep these same-object aliases until
supported controller, CLI, Agent runtime and external consumers migrate to those
owners; remove this facade only in an incompatible API release after migration.
Filesystem setup, executable probes and gateway creation remain call-time work.
"""

from .claude_launch import (
    build_claude_command as build_claude_command,
)
from .claude_launch import (
    build_claude_mcp_config as build_claude_mcp_config,
)
from .codex_launch import (
    build_codex_command as build_codex_command,
)
from .codex_launch import (
    prepare_codex_gateway as prepare_codex_gateway,
)
from .codex_selection import (
    CodexConfigurationError as CodexConfigurationError,
)
from .launch_bootstrap import (
    install_packaged_codex_skills as install_packaged_codex_skills,
)
from .launch_bootstrap import (
    private_codex_home as private_codex_home,
)
from .launch_environment import (
    prepare_terminal_environment as prepare_terminal_environment,
)
from .launch_environment import (
    sanitized_terminal_environment as sanitized_terminal_environment,
)
from .launch_executable import (
    prepare_agent_runtime_environment as prepare_agent_runtime_environment,
)
from .launch_executable import (
    production_python as production_python,
)
from .launch_executable import (
    resolve_agent_executable as resolve_agent_executable,
)
from .launch_executable import (
    resolve_executable as resolve_executable,
)
from .launch_policy import (
    LOCAL_HELP_INSTRUCTIONS as LOCAL_HELP_INSTRUCTIONS,
)
from .launch_policy import (
    SKILL_PREFLIGHT_INSTRUCTIONS as SKILL_PREFLIGHT_INSTRUCTIONS,
)
from .launch_policy import (
    assistant_instructions as assistant_instructions,
)

__all__ = [
    "build_claude_command",
    "build_claude_mcp_config",
    "build_codex_command",
    "install_packaged_codex_skills",
    "prepare_terminal_environment",
    "private_codex_home",
    "production_python",
    "prepare_agent_runtime_environment",
    "resolve_agent_executable",
    "resolve_executable",
    "sanitized_terminal_environment",
]
