"""Read current SiCo settings; reject retired names without inspecting their values."""

from __future__ import annotations

from collections.abc import Mapping


# Retired names are diagnostics and child-environment sanitation, never inputs.
# Historical record keys are owned by their readers and are not rewritten.
RETIRED_NAMES = {
    "SICO_PYTHON": ("CAD_PYTHON",),
    "SICO_PYTHON_ROOT": ("CAD_PYTHON_ROOT",),
    "SICO_SYSTEM_LD_LIBRARY_PATH": ("CAD_SYSTEM_LD_LIBRARY_PATH",),
    "SICO_AI_CONTEXT": ("CAD_AI_CONTEXT",),
    "SICO_AI_RELEASE_ROOT": ("CAD_AI_RELEASE_ROOT",),
    "SICO_AI_SKILL_REFERENCE_ROOT": ("CAD_AI_SKILL_REFERENCE_ROOT",),
    "SICO_AI_DIAGNOSTIC_LOG": ("CAD_AI_DIAGNOSTIC_LOG",),
    "SICO_AI_ROUTER_DIAGNOSTIC_LOG": ("CAD_AI_ROUTER_DIAGNOSTIC_LOG",),
    "SICO_AI_PLATFORM": ("CAD_AI_PLATFORM",),
    "SICO_AI_OS_RELEASE_FILE": ("CAD_AI_OS_RELEASE_FILE",),
    "SICO_AI_QTERMWIDGET_RUNTIME": ("CAD_AI_QTERMWIDGET_RUNTIME",),
    "SICO_AI_PYQT_ROOT": ("CAD_AI_PYQT_ROOT",),
    "SICO_AI_PYTHON_LIBRARY_PATH": ("CAD_AI_PYTHON_LIBRARY_PATH", "CAD_CODEX_PYTHON_LIBRARY_PATH"),
    "SICO_AI_QT_PLUGIN_PATH": ("CAD_AI_QT_PLUGIN_PATH", "CAD_CODEX_QT_PLUGIN_PATH"),
    "SICO_AI_TERMINAL": ("CAD_AI_TERMINAL", "CAD_CODEX_TERMINAL"),
    "SICO_AI_TERMINAL_COLOR_SCHEME": ("CAD_AI_TERMINAL_COLOR_SCHEME",),
    "SICO_AI_TEST_OUTPUT": ("CAD_AI_TEST_OUTPUT",),
    "SICO_CODEX_CLI": ("CAD_CODEX_CLI", "CAD_AGENT_CODEX_CLI"),
    "SICO_CODEX_HOME": ("CAD_CODEX_HOME",),
    "SICO_CODEX_MODEL": ("CAD_CODEX_MODEL",),
    "SICO_CODEX_API_BASE_URL": ("CAD_CODEX_API_BASE_URL",),
    "SICO_CODEX_API_KEY": ("CAD_CODEX_API_KEY",),
    "SICO_CLAUDE_CLI": ("CAD_CLAUDE_CLI",),
    "SICO_CLAUDE_MODEL": ("CAD_CLAUDE_MODEL",),
    "SICO_MCP_PORT": ("CAD_COPILOT_MCP_PORT", "CAD_STUDIO_MCP_PORT"),
    "SICO_MCP_TOKEN": ("CAD_COPILOT_MCP_TOKEN", "CAD_STUDIO_MCP_TOKEN"),
    "SICO_MCP_TIMEOUT": ("CAD_AGENT_MCP_TIMEOUT", "CAD_COPILOT_MCP_TIMEOUT", "CAD_STUDIO_MCP_TIMEOUT"),
    "SICO_AI_REQUEST_TIMEOUT": ("CAD_AI_REQUEST_TIMEOUT", "CAD_CODEX_REQUEST_TIMEOUT"),
    "SICO_AI_MCP_TIMEOUT": ("CAD_AI_MCP_TIMEOUT", "CAD_CODEX_MCP_TIMEOUT"),
    "SICO_SKILL_TIMEOUT": ("CAD_AGENT_SKILL_TIMEOUT",),
    "SICO_AI_DEVICE_CATALOG": ("CAD_AI_DEVICE_CATALOG",),
    "SICO_CIRCUIT_TEMPLATES_DIR": ("CAD_CIRCUIT_TEMPLATES_DIR", "CAD_AI_TEMPLATE_ROOT"),
}


def value(
    environment: Mapping[str, str],
    current: str,
    legacy: tuple[str, ...] = (),
    default=None,
):
    """Read one current spelling; old-only configuration requires migration."""
    if current in environment:
        return environment[current]
    retired = next((name for name in legacy if name in environment), None)
    if retired is not None:
        raise ValueError(f"{retired} was removed after SiCo v0.0.1; set {current}")
    return default


def read(environment, current, default=None):
    """Read a current setting with retired-input diagnostics."""
    return value(environment, current, RETIRED_NAMES[current], default)


def publish(environment, current, configured):
    """Export only the current spelling, removing its obsolete aliases."""
    for old in RETIRED_NAMES[current]:
        environment.pop(old, None)
    environment[current] = configured


def promote(environment, *names):
    """Normalize configured process settings, preserving explicit empty values."""
    for current in names:
        configured = read(environment, current)
        if configured is not None:
            publish(environment, current, configured)
