"""Immutable structural connectivity evidence and comparison findings."""

from __future__ import annotations

from dataclasses import dataclass


class ConnectivityParseError(ValueError):
    """Input structure cannot be parsed unambiguously."""


@dataclass(frozen=True)
class Port:
    name: str
    direction: str = ""


@dataclass(frozen=True)
class Instance:
    name: str
    master: str
    connections: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Module:
    name: str
    ports: tuple[Port, ...]
    instances: tuple[Instance, ...]


@dataclass(frozen=True)
class Structure:
    modules: tuple[Module, ...]
    globals: tuple[tuple[str, str], ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    scope: str
    detail: str
    expected: object | None = None
    actual: object | None = None
    instance: str | None = None
    terminal: str | None = None

    def as_dict(self) -> dict[str, object]:
        item: dict[str, object] = {
            "code": self.code,
            "severity": self.severity,
            "scope": self.scope,
            "detail": self.detail,
        }
        for key, value in (
            ("expected", self.expected),
            ("actual", self.actual),
            ("instance", self.instance),
            ("terminal", self.terminal),
        ):
            if value is not None:
                item[key] = value
        return item
