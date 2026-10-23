"""Build and load private symbol-family registries."""

from __future__ import annotations

import json
from pathlib import Path

from .template_schema import TemplateError, canonical
from .template_symbol_family import FAMILY_FORMAT, build_family, dedupe_families

INDEX_FORMAT = "sico.symbol.family.index.v1"


def build_registry(records, specs):
    """Build canonical families and merged cell hints from captured records.

    ``records`` maps ``(library, cell)`` to a template record; ``specs`` lists
    ``library``, ``cell``, ``family_id``, optional ``cell_hints`` and optional
    ``roles`` for power/ground placement.
    """
    built, source_hints = [], {}
    for spec in specs:
        key = (spec["library"], spec["cell"])
        record = records.get(key)
        if record is None:
            raise TemplateError(f"family source record missing: {key[0]}/{key[1]}")
        built.append(
            build_family(
                record,
                spec["family_id"],
                roles=spec.get("roles"),
                pg_layout=spec.get("pg_layout", "source"),
                side_overrides=spec.get("side_overrides", {}),
            )
        )
        source_hints[key] = list(spec.get("cell_hints", []))
    families = dedupe_families(built)
    hints = {}
    for family in families:
        merged = []
        for alias in family.get("aliases", []):
            source = alias.get("source") or {}
            merged.extend(source_hints.get((source.get("lib"), source.get("cell")), []))
        hints[family["family_id"]] = sorted(set(merged))
    return families, hints


def write_registry(root, families, hints):
    """Write canonical family files and a private index under ``root``."""
    ids = [family.get("family_id") for family in families]
    if any(
        not isinstance(family_id, str) or not family_id or Path(family_id).name != family_id
        for family_id in ids
    ):
        raise TemplateError("invalid symbol family id")
    if len(ids) != len(set(ids)):
        raise TemplateError("duplicate canonical symbol family id")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    index = {"format": INDEX_FORMAT, "families": {}}
    for family, family_id in zip(families, ids):
        path = root / (family_id + ".json")
        path.write_text(canonical(family) + "\n")
        index["families"][family_id] = {
            "file": path.name,
            "cell_hints": sorted(set(hints.get(family_id, []))),
        }
    (root / "index.json").write_text(canonical(index) + "\n")
    return index


def read_registry(root):
    """Load a private index and every referenced family; absent index is empty."""
    root = Path(root)
    index_path = root / "index.json"
    if not index_path.is_file():
        return {}, {}
    try:
        index = json.loads(index_path.read_text())
    except (OSError, ValueError) as exc:
        raise TemplateError("cannot read symbol family index: " + str(exc)) from exc
    if index.get("format") != INDEX_FORMAT or not isinstance(index.get("families"), dict):
        raise TemplateError("unsupported symbol family index")
    families, hints = {}, {}
    for family_id, entry in index["families"].items():
        file_name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            raise TemplateError("invalid symbol family index entry")
        path = root / file_name
        if not path.is_file():
            raise TemplateError("symbol family file missing: " + file_name)
        try:
            family = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise TemplateError("cannot read symbol family: " + str(exc)) from exc
        if family.get("format") != FAMILY_FORMAT:
            raise TemplateError("unsupported symbol family format")
        families[family_id] = family
        hints[family_id] = list(entry.get("cell_hints", []))
    return families, hints
