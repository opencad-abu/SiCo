"""Stable JSON presentation for command output."""

from __future__ import annotations

import json


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
