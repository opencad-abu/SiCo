"""Validate reviewed compatibility records without importing product modules."""

from __future__ import annotations

import ast
import json
from pathlib import Path

try:
    from .a_philosophy_baseline import relative
    from .a_philosophy_sources import read_python
    from .a_philosophy_exports import check_export_groups
except ImportError:
    from a_philosophy_baseline import relative
    from a_philosophy_sources import read_python
    from a_philosophy_exports import check_export_groups


def load_compatibility(root):
    """One registry, partitioned by owning area for reviewable source records."""
    payload = json.loads((root / "tools/utility/a_philosophy_compatibility.json").read_text())
    entries = list(payload.get("compatibility", []))
    seen = set()
    for name in payload.get("includes", []):
        if not relative(name) or name in seen:
            raise ValueError(f"invalid/duplicate compatibility include: {name}")
        seen.add(name)
        part = json.loads((root / name).read_text())
        if part.get("schema_version") != 1 or not isinstance(part.get("compatibility"), list):
            raise ValueError(f"unsupported compatibility include: {name}")
        entries.extend(part["compatibility"])
    return {**payload, "compatibility": entries}


def _strings(value):
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def validate_reference(root, value):
    """Validate a repository file or concrete definition used as audit evidence."""
    path, _, symbol = value.partition(":")
    if not relative(path) or not (root / path).is_file():
        raise ValueError(f"missing/invalid compatibility reference: {value}")
    if symbol:
        tree = ast.parse(read_python(root / path), filename=path)
        for name in symbol.split("."):
            found = next(
                (
                    node
                    for node in tree.body
                    if isinstance(
                        node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                    )
                    and node.name == name
                ),
                None,
            )
            if found is None:
                raise ValueError(f"missing compatibility definition: {value}")
            tree = found


def check_compatibility(root: Path, payload):
    errors, seen = [], set()
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["unsupported compatibility schema"]
    entries = payload.get("compatibility")
    if not isinstance(entries, list) or not entries:
        return ["compatibility must be a non-empty list"]
    for index, row in enumerate(entries):
        label = f"compatibility[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{label} must be an object")
            continue
        for field in ("legacy", "replacement", "source", "owner", "exit_condition"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(f"{label}.{field} must be non-empty")
        legacy = row.get("legacy")
        if isinstance(legacy, str):
            if legacy in seen:
                errors.append(f"duplicate legacy entry: {legacy}")
            seen.add(legacy)
        if row.get("legacy") == row.get("replacement"):
            errors.append(f"{label} replacement must differ from legacy")
        if not isinstance(row.get("status"), str) or row["status"] not in {
            "retained",
            "tombstone",
            "removed",
        }:
            errors.append(f"{label} invalid compatibility status")
        if type(row.get("allow_exposure")) is not bool:
            errors.append(f"{label}.allow_exposure must be boolean")
        elif row.get("status") in ("removed", "tombstone") and row["allow_exposure"]:
            errors.append(f"{label} retired operation cannot allow exposure")
        for field in ("consumers", "evidence"):
            if field == "consumers" and row.get(field) == [] and isinstance(row.get("external_consumers"), str) and row["external_consumers"].strip():
                continue
            if not _strings(row.get(field)):
                errors.append(f"{label}.{field} must contain non-empty strings")
        references = [(field, row.get(field)) for field in ("source", "owner", "replacement")]
        if _strings(row.get("evidence")):
            references.extend(("evidence", ref) for ref in row["evidence"])
        if _strings(row.get("consumers")):
            references.extend(("consumers", ref) for ref in row["consumers"])
        for field, ref in references:
            if not isinstance(ref, str):
                continue
            if row.get("status") == "removed" and field == "source":
                retired_path, separator, _ = ref.partition(":")
                if relative(retired_path) and not separator and not (root / retired_path).exists():
                    continue  # A removed file remains traceable in the retirement registry.
            try:
                validate_reference(root, ref)
            except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
                errors.append(f"{label}: {exc}")
    if not errors:
        errors.extend(check_export_groups(root, entries, payload.get("search_paths", [])))
    return errors


def compatibility_candidates(root, rows, registered):
    """Candidates are review clues; the word legacy alone proves no violation."""
    covered = {row["source"].partition(":")[0] for row in registered}
    candidates = []
    for row in rows:
        if row.test or row.vendored or not row.path.endswith(".py"):
            continue
        try:
            tree = ast.parse(read_python(root / row.path), filename=row.path)
        except (OSError, SyntaxError, UnicodeError):
            continue  # Already a hard failure in the AST scan.
        doc = (ast.get_docstring(tree) or "").lower()
        if any(
            word in doc for word in ("compatibility", "facade", "legacy", "deprecated")
        ):
            candidates.append({"path": row.path, "has_registration": row.path in covered})
    return candidates
