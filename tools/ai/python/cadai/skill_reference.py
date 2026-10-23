"""Read-only access to the bundled Cadence SKILL API reference."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .skill_reference_store import ReferenceStore, SkillReferenceUnavailable
from .skill_reference_tools import (
    MAX_DOCUMENT_CHARS,
    MAX_EXACT_MATCHES,
    MAX_QUERY_CHARS,
    MAX_SEARCH_RESULTS,
    SKILL_REFERENCE_TOOL_NAMES,
    SKILL_REFERENCE_TOOLS,
    default_reference_root,
    normalize_reference_root,
)

SEARCH_KINDS = frozenset({"function", "topic", "alias", "toc"})


class SkillReferenceArgumentError(ValueError):
    pass


def _bounded_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise SkillReferenceArgumentError(f"{name} must be a string")
    value = value.strip()
    if not value or len(value) > MAX_QUERY_CHARS:
        raise SkillReferenceArgumentError(
            f"{name} must contain 1 to {MAX_QUERY_CHARS} characters"
        )
    return value


def _clamped_integer(
    arguments: dict[str, Any], name: str, default: int, minimum: int, maximum: int
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SkillReferenceArgumentError(f"{name} must be an integer")
    return max(minimum, min(value, maximum))


def _reject_extra(arguments: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(arguments) - allowed)
    if extra:
        raise SkillReferenceArgumentError(f"unexpected arguments: {', '.join(extra)}")


def _field(row: Mapping[str, str], name: str) -> str:
    try:
        value = row[name]
    except (KeyError, IndexError):
        return ""
    return value if isinstance(value, str) else ""


def _json_field(row: Mapping[str, str], name: str) -> list[Any]:
    try:
        value = json.loads(_field(row, name) or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _summary(body: str, limit: int = 600) -> str:
    marker = "\nDescription\n"
    text = body.split(marker, 1)[1] if marker in body else body
    for heading in ("\nArguments\n", "\nValue Returned\n", "\nExamples\n", "\nRelated Topics\n"):
        text = text.split(heading, 1)[0]
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3].rstrip() + "..."


def _score(
    row: Mapping[str, str], query: str, tokens: list[str]
) -> tuple[int, int, int, str, str]:
    function = row["name"].casefold()
    reference_id = row["reference_id"].casefold()
    usage = row["usage"].casefold()
    title = row["doc_title"].casefold()
    body = row["body"].casefold()
    if reference_id == query:
        rank = 0
    elif function == query:
        rank = 1
    elif function.startswith(query):
        rank = 2
    elif query in function:
        rank = 3
    elif query in usage:
        rank = 4
    else:
        rank = 5
    relevance = sum(
        200 * (token in function)
        + 100 * (token in reference_id)
        + 50 * (token in usage)
        + 20 * (token in title)
        + min(body.count(token), 10)
        for token in tokens
    )
    return rank, -relevance, len(function), function, reference_id


class SkillReference:
    def __init__(self, root: Path | None = None, *, workspace=None):
        self._configured_root = root
        self._environment = dict(os.environ)
        if workspace is not None:
            self._environment["SICO_AI_WORKSPACE"] = str(workspace)
        self._loaded_store = None

    @property
    def root(self):
        try:
            return (default_reference_root(self._environment) if self._configured_root is None
                    else Path(self._configured_root))
        except (OSError, ValueError, RuntimeError) as exc:
            raise SkillReferenceUnavailable(str(exc)) from exc

    @property
    def _store(self):
        if self._loaded_store is None:
            self._loaded_store = ReferenceStore(self.root)
        return self._loaded_store

    @property
    def database(self):
        return self._store.database

    @property
    def fallback(self):
        return self._store.fallback

    @property
    def backend(self) -> str | None:
        """Selected reference backend (``sqlite`` or ``json-fallback``)."""
        return self._loaded_store.backend if self._loaded_store is not None else None

    def close(self) -> None:
        if self._loaded_store is not None:
            self._loaded_store.close()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_skill_api":
            return self.search(arguments)
        if name == "get_skill_api":
            return self.get(arguments)
        raise SkillReferenceArgumentError(f"unknown SKILL reference tool: {name}")

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra(arguments, {"query", "limit", "kind", "domain", "family"})
        query = _bounded_string(arguments, "query")
        limit = _clamped_integer(arguments, "limit", 5, 1, MAX_SEARCH_RESULTS)
        filters = {}
        for field, maximum in (("kind", 16), ("domain", 64), ("family", 32)):
            value = arguments.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise SkillReferenceArgumentError(
                    f"{field} must contain 1 to {maximum} characters"
                )
            filters[field] = value.strip()
        if filters.get("kind") not in (None, *SEARCH_KINDS):
            raise SkillReferenceArgumentError(
                "kind must be function, topic or toc"
            )
        tokens = [token for token in query.split() if token]
        rows = self._store.search(tokens, filters)
        normalized = query.casefold()
        normalized_tokens = [token.casefold() for token in tokens]
        rows.sort(key=lambda row: _score(row, normalized, normalized_tokens))
        return {
            "query": query,
            "total_matches": len(rows),
            "matches": [self._search_result(row) for row in rows[:limit]],
            "reference": self._store.metadata(),
        }

    def get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        _reject_extra(arguments, {"name", "max_chars"})
        name = _bounded_string(arguments, "name")
        max_chars = _clamped_integer(
            arguments, "max_chars", MAX_DOCUMENT_CHARS, 1_024, MAX_DOCUMENT_CHARS
        )
        rows = self._store.exact_reference(name)
        if not rows:
            rows = self._store.exact_name(name)
        if not rows:
            raise SkillReferenceArgumentError(self._no_match(name))
        normalized = name.casefold()
        rows.sort(key=lambda row: _score(row, normalized, [normalized]))
        total_matches = len(rows)
        rows = rows[:MAX_EXACT_MATCHES]
        return {
            "query": name,
            "total_matches": total_matches,
            "truncated": total_matches > len(rows),
            "matches": [self._document(row, max_chars) for row in rows],
            "reference": self._store.metadata(),
        }

    def _no_match(self, name: str):
        normalized = name.casefold()
        rows = sorted(
            self._store.search([name]), key=lambda row: _score(row, normalized, [normalized])
        )
        names = list(dict.fromkeys(row["name"] for row in rows[:3]))
        closest = "; closest: " + ", ".join(names) if names else ""
        return f"no exact SKILL API match for {name!r}{closest}; call search_skill_api for details"

    @staticmethod
    def _search_result(row: Mapping[str, str]) -> dict[str, Any]:
        result = {
            "name": row["name"],
            "reference_id": row["reference_id"],
            "usage": row["usage"],
            "summary": _summary(row["body"]),
            "doc_set": row["doc_set"],
            "document": row["document"],
            "product_version": row["product_version"],
        }
        for field in ("kind", "domain", "family"):
            value = _field(row, field)
            if value:
                result[field] = value
        description = _field(row, "description")
        if description and _field(row, "kind"):
            result["description"] = _summary(description)
        return result

    @staticmethod
    def _document(row: Mapping[str, str], max_chars: int) -> dict[str, Any]:
        body = row["body"]
        result = {
            "name": row["name"],
            "reference_id": row["reference_id"],
            "usage": row["usage"],
            "documentation": body[:max_chars],
            "truncated": len(body) > max_chars,
            "doc_set": row["doc_set"],
            "document": row["document"],
            "doc_title": row["doc_title"],
            "product_version": row["product_version"],
        }
        for field in ("kind", "domain", "family", "source_kind"):
            value = _field(row, field)
            if value:
                result[field] = value
        for field, output in (
            ("arguments_json", "arguments"),
            ("returns_json", "returns"),
            ("examples_json", "examples"),
            ("related_json", "related"),
        ):
            value = _json_field(row, field)
            if value:
                result[output] = value
        return result


__all__ = [
    "SKILL_REFERENCE_TOOL_NAMES",
    "SKILL_REFERENCE_TOOLS",
    "SkillReference",
    "SkillReferenceArgumentError",
    "SkillReferenceUnavailable",
    "default_reference_root",
    "normalize_reference_root",
]
