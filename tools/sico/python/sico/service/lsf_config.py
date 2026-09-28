"""Explicit site settings for batch execution, separate from interactive lsf-session."""

import math
import re
from dataclasses import dataclass
from pathlib import Path

from ..transport.framing import ProtocolError


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise ProtocolError("Invalid LSF identity")
    return value


def lsf_settings(row):
    try:
        return LsfConfig(**row)
    except ProtocolError:
        raise
    except (TypeError, ValueError) as exc:
        raise ProtocolError('Invalid captured LSF configuration') from exc


@dataclass(frozen=True)
class LsfConfig:
    cluster: str
    owner: str
    queue: str
    shared_root: str
    node_command: tuple
    bsub: str
    bjobs: str
    bkill: str
    bhist: str
    lsid: str
    slots: int = 1
    wall_minutes: int = 5
    resource: str = ""
    command_timeout: float = 5
    poll_seconds: float = .2

    def __post_init__(self):
        for name in ("cluster", "owner", "queue"):
            label(getattr(self, name))
        for name in ("shared_root", "bsub", "bjobs", "bkill", "bhist", "lsid"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not Path(value).is_absolute()
                    or any(c in value for c in "\0\r\n")):
                raise ValueError("LSF paths must be explicit and absolute")
        if not isinstance(self.node_command, (tuple, list)):
            raise ProtocolError('Node runtime command must be an argv list')
        command = tuple(self.node_command)
        if (not command or len(command) > 16 or not Path(command[0]).is_absolute()
                or any(not isinstance(v, str) or len(v) > 4096
                       or any(c in v for c in "\0\r\n") for v in command)):
            raise ValueError("Invalid LSF node runtime argv")
        object.__setattr__(self, "node_command", command)
        for value, limit in ((self.slots, 256), (self.wall_minutes, 1440)):
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError("Invalid LSF resource budget")
        for value in (self.command_timeout, self.poll_seconds):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600:
                raise ValueError("Invalid LSF deadline")
        if (not isinstance(self.resource, str) or len(self.resource) > 2048
                or any(c in self.resource for c in "\0\r\n")):
            raise ValueError("Invalid site LSF resource expression")

    def shared(self, path):
        path, root = Path(path).absolute(), Path(self.shared_root).absolute()
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("LSF job files must be inside the configured shared root")
        return path  # Keep the site's NAS alias in argv and persisted references.
