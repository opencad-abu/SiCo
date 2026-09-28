"""Case-insensitive browser substring matching."""

from __future__ import annotations




def matches(value: str, filter_text: str) -> bool:
    needle = filter_text.strip().casefold()
    return not needle or needle in value.casefold()
