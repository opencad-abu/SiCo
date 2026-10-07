"""Strict local qualification fixture decoding."""

from __future__ import annotations

import json
from pathlib import Path
from .errors import AivwError


class _DuplicateCliJsonKey(ValueError):
    """Raised when a strict CLI fixture repeats a JSON object name."""


def reject_cli_json_constant(value: str) -> None:
    raise ValueError("non-finite JSON constant: %s" % value)


def reject_cli_json_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateCliJsonKey("duplicate JSON field: %s" % key)
        result[key] = value
    return result


def load_fixture(path_value: str, label: str) -> object:
    """Load one strict JSON or JSONL fixture without accepting aliases."""

    path = Path(path_value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise AivwError("%s must be a regular non-symlink file" % label)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AivwError("cannot read %s: %s" % (label, exc)) from exc
    try:
        if path.suffix.lower() == ".jsonl":
            values: list[object] = []
            for index, line in enumerate(raw.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    values.append(
                        json.loads(
                            line,
                            parse_constant=reject_cli_json_constant,
                            object_pairs_hook=reject_cli_json_duplicates,
                        )
                    )
                except (TypeError, ValueError, UnicodeError) as exc:
                    raise AivwError(
                        "invalid %s JSONL record %d: %s" % (label, index, exc)
                    ) from exc
            return values
        return json.loads(
            raw,
            parse_constant=reject_cli_json_constant,
            object_pairs_hook=reject_cli_json_duplicates,
        )
    except AivwError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AivwError("invalid %s JSON: %s" % (label, exc)) from exc


def fixture_items(value: object, *, wrapper: str, label: str) -> list[object]:
    if isinstance(value, dict) and wrapper in value:
        if set(value) != {wrapper}:
            unknown = sorted(str(key) for key in value if key != wrapper)
            raise AivwError(
                "%s wrapper contains unknown fields: %s" % (label, ", ".join(unknown))
            )
        value = value[wrapper]
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return [value]
    raise AivwError("%s fixture must contain an object or array" % label)


def load_provider_requests(path_value: str) -> tuple[object, ...]:
    from .agent.backend import ProviderRequest

    raw = load_fixture(path_value, "requests")
    items = fixture_items(raw, wrapper="requests", label="requests")
    if not items:
        raise AivwError("requests fixture must not be empty")
    requests: list[ProviderRequest] = []
    for index, item in enumerate(items):
        try:
            requests.append(ProviderRequest.from_dict(item))
        except ValueError as exc:
            raise AivwError("invalid request %d: %s" % (index, exc)) from exc
    return tuple(requests)


def load_replay_records(path_value: str) -> tuple[object, ...]:
    from .agent.providers.replay import ReplayRecord

    raw = load_fixture(path_value, "records")
    items = fixture_items(raw, wrapper="records", label="records")
    if not items:
        raise AivwError("records fixture must not be empty")
    records: list[ReplayRecord] = []
    for index, item in enumerate(items):
        try:
            records.append(ReplayRecord.from_mapping(item))
        except ValueError as exc:
            raise AivwError("invalid record %d: %s" % (index, exc)) from exc
    return tuple(records)
