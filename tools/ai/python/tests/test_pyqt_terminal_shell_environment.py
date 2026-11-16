"""Frontend loader variables never leak into the interactive shell."""

from __future__ import annotations

from cad_ai_terminal.shell_environment import SHELL_ENV_DROP, sanitize_shell_environment


def test_sanitize_drops_only_frontend_loader_variables_from_the_target():
    environment = {
        "PATH": "/usr/bin",
        "HOME": "/home/user",
        **{name: "loader" for name in SHELL_ENV_DROP},
    }
    sanitize_shell_environment(environment)
    assert environment == {"PATH": "/usr/bin", "HOME": "/home/user"}


def test_sanitize_is_idempotent_and_tolerates_absent_names():
    environment = {"PATH": "/usr/bin"}
    sanitize_shell_environment(environment)
    sanitize_shell_environment(environment)
    assert environment == {"PATH": "/usr/bin"}
