"""Detect credential-bearing fields in qualification evidence."""

from __future__ import annotations

import re
from typing import Mapping

_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|password|passwd|private[_-]?key|session[_-]?token|credential|secret)",
    re.IGNORECASE,
)


_SECRET_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._~+/=-]{8,}|-----BEGIN[^\n]+PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)


_ALLOWED_SECRET_REFERENCE_KEYS = frozenset(
    {"secret_reference", "secret_policy", "secret_present", "redaction"}
)


def scan_secret_leaks(value: object, path: str = "value") -> tuple[str, ...]:
    """Return observable paths that contain credential-like material."""

    findings: list[str] = []
    active: set[int] = set()

    def walk(item: object, location: str) -> None:
        if isinstance(item, str):
            if _SECRET_VALUE.search(item):
                findings.append(location)
            return
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                findings.append(location + ":cycle")
                return
            active.add(identity)
            try:
                for key, child in item.items():
                    key_text = str(key)
                    key_location = location + "." + key_text
                    if _SECRET_KEY.search(key_text) and key_text.casefold() not in _ALLOWED_SECRET_REFERENCE_KEYS:
                        # A reference is safe only when it is explicitly shaped
                        # as {source: environment, name: VAR}.
                        if not (
                            key_text.casefold() == "secret_reference"
                            and isinstance(child, Mapping)
                            and child.get("source") == "environment"
                            and isinstance(child.get("name"), str)
                            and re.fullmatch(r"[A-Z][A-Z0-9_]*", child["name"]) is not None
                        ):
                            findings.append(key_location)
                    walk(child, key_location)
            finally:
                active.remove(identity)
            return
        if isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in active:
                findings.append(location + ":cycle")
                return
            active.add(identity)
            try:
                for index, child in enumerate(item):
                    walk(child, "%s[%d]" % (location, index))
            finally:
                active.remove(identity)

    walk(value, path)
    return tuple(sorted(set(findings)))
