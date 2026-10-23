"""Captured background-runtime values; tool availability belongs to the service."""

from dataclasses import dataclass
from pathlib import Path

from cadai.json_values import ProtocolError


@dataclass(frozen=True)
class BackgroundSettings:
    virtuoso: Path
    context: Path
    context_sha256: str
    version: str
    libraries: dict
    xvfb: Path = Path('/usr/bin/Xvfb')

    def __post_init__(self):
        for field in ('virtuoso', 'context', 'xvfb'):
            object.__setattr__(self, field, Path(getattr(self, field)))
        object.__setattr__(self, 'libraries', dict(self.libraries))

    @classmethod
    def from_record(cls, row):
        required = {'virtuoso', 'context', 'context_sha256', 'version', 'libraries'}
        if not isinstance(row, dict) or not required <= set(row) <= required | {'xvfb'}:
            raise ProtocolError('Invalid background runtime configuration')
        try:
            return cls(**row)
        except (TypeError, ValueError) as exc:
            raise ProtocolError('Invalid background runtime values') from exc
