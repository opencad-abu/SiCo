"""Command-scoped shell policy for SiCo-owned terminal commands.

This is deliberately not a general interactive user shell. SiCo/Codex commands
and EDA launchers use one non-login Bash with an explicitly supplied environment;
the user's GNOME terminal and login/site startup files remain outside this scope.
"""

from dataclasses import dataclass
from pathlib import Path

from ..core.contracts import identifier
from ..core.terminal_session import TerminalClaim, TerminalRef

COMMAND_SHELL = "/bin/bash"
COMMAND_MODE = "pty"
DEFAULT_QUEUE_LIMIT = 64


@dataclass(frozen=True)
class CommandShellProfile:
    """One explicit command-terminal policy, never a login-shell facade."""

    shell: str = COMMAND_SHELL
    mode: str = COMMAND_MODE
    queue_limit: int = DEFAULT_QUEUE_LIMIT
    sources_user_startup: bool = False
    purpose: str = "sico.command"

    def __post_init__(self):
        if (self.shell != COMMAND_SHELL or self.mode != COMMAND_MODE
                or type(self.queue_limit) is not int or not 1 <= self.queue_limit <= 256
                or self.sources_user_startup is not False
                or self.purpose != "sico.command"):
            raise ValueError("Unsupported SiCo command-shell profile")

    def record(self):
        return dict(shell=self.shell, mode=self.mode, queue_limit=self.queue_limit,
                    sources_user_startup=self.sources_user_startup, purpose=self.purpose)


def command_claim(session_id, cwd, environment_id, *, terminal_id="terminal",
                  queue_limit=DEFAULT_QUEUE_LIMIT):
    """Build the one claim used by a product session's command terminal."""
    identifier(session_id)
    identifier(terminal_id)
    identifier(environment_id)
    path = Path(cwd)
    if not path.is_absolute():
        raise ValueError("Terminal command cwd must be absolute")
    profile = CommandShellProfile(queue_limit=queue_limit)
    return TerminalClaim(
        TerminalRef(session_id, terminal_id), profile.shell, str(path),
        environment_id, mode=profile.mode, queue_limit=profile.queue_limit,
    )
