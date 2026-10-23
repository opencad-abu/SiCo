"""Build a private symbol-family registry from explicit catalog records.

This development entry keeps the family roster in a private manifest.  Every
source is pinned by ``template_ref`` so a build cannot silently pick a newer
catalog capture.  The manifest and generated registry stay outside Git.

Run as::

    PYTHONPATH=tools/ai/python:tools/common/python python -B -s \
        -m cadai.template_symbol_family_build build \
        --catalog PATH/catalog.sqlite3 --manifest families.json \
        --output PATH/symbol_families --report PATH/build-report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .template_catalog import TemplateCatalog
from .template_schema import TemplateError, canonical
from .template_symbol_family_registry import build_registry, read_registry, write_registry

MANIFEST_FORMAT = "sico.symbol.family.build.v1"
REPORT_FORMAT = "sico.symbol.family.build.report.v1"
LAYOUT_ROLES = {"power", "ground", "signal"}


def _string(value, field, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TemplateError("symbol family manifest requires text " + field)
    return value.strip()


def _string_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise TemplateError("symbol family manifest requires text list " + field)
    return sorted(set(value))


def read_manifest(path):
    """Read and validate one explicit family roster."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TemplateError("cannot read symbol family manifest: " + str(exc)) from exc
    if not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT:
        raise TemplateError("unsupported symbol family manifest format")
    rows = data.get("families")
    if not isinstance(rows, list) or not rows:
        raise TemplateError("symbol family manifest requires families")
    specs, references = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise TemplateError("invalid symbol family manifest entry")
        reference = _string(row.get("template_ref"), "template_ref")
        if reference in references:
            raise TemplateError("duplicate symbol family source: " + reference)
        references.add(reference)
        roles = row.get("roles") or {}
        if not isinstance(roles, dict) or any(
            not isinstance(name, str) or role not in LAYOUT_ROLES for name, role in roles.items()
        ):
            raise TemplateError("symbol family roles must map ports to power/ground/signal")
        pg_layout = row.get("pg_layout", "source")
        if pg_layout not in {"source", "center"}:
            raise TemplateError("symbol family pg_layout must be source or center")
        side_overrides = row.get("side_overrides") or {}
        if not isinstance(side_overrides, dict) or any(
            not isinstance(name, str) or side not in {"left", "right", "top", "bottom"}
            for name, side in side_overrides.items()
        ):
            raise TemplateError("symbol family side_overrides must map ports to four sides")
        specs.append(
            {
                "family_id": _string(row.get("family_id"), "family_id"),
                "template_ref": reference,
                "library": _string(row.get("library"), "library", optional=True),
                "cell": _string(row.get("cell"), "cell", optional=True),
                "cell_hints": _string_list(row.get("cell_hints"), "cell_hints"),
                "roles": dict(sorted(roles.items())),
                "pg_layout": pg_layout,
                "side_overrides": dict(sorted(side_overrides.items())),
            }
        )
    required = _string_list(data.get("required_families"), "required_families")
    return {"specs": specs, "required_families": required}


def _catalog(path):
    path = Path(path).resolve()
    if path.name == "catalog.sqlite3":
        root = path.parent
    elif path.parent.name == "catalogs" and path.suffix == ".sqlite3":
        root = path.parent.parent
    else:
        raise TemplateError("catalog must be catalog.sqlite3 or catalogs/*.sqlite3")
    if not path.is_file():
        raise TemplateError("symbol family catalog is missing: " + str(path))
    return TemplateCatalog(root=root), path


def _records(catalog, catalog_path, specs):
    records, build_specs = {}, []
    for spec in specs:
        record, origins = catalog.locate(spec["template_ref"])
        locations = {Path(item["catalog"]).resolve() for item in origins["available_from"]}
        if catalog_path not in locations:
            raise TemplateError(
                "template_ref is absent from the selected catalog: " + spec["template_ref"]
            )
        source = record.get("source") or {}
        library, cell = source.get("lib"), source.get("cell")
        if not library or not cell:
            raise TemplateError("family source has no library/cell identity")
        if spec["library"] and spec["library"] != library:
            raise TemplateError("family source library differs from manifest")
        if spec["cell"] and spec["cell"] != cell:
            raise TemplateError("family source cell differs from manifest")
        ports = {port.get("name") for port in (record.get("topology") or {}).get("ports", [])}
        if set(spec["roles"]) - ports:
            raise TemplateError("family roles reference ports absent from the source schematic")
        key = (library, cell)
        if key in records:
            raise TemplateError("family source is listed more than once: " + "/".join(key))
        records[key] = record
        build_specs.append(
            {
                "library": library,
                "cell": cell,
                "template_ref": spec["template_ref"],
                "family_id": spec["family_id"],
                "cell_hints": spec["cell_hints"],
                "roles": spec["roles"],
                "pg_layout": spec.get("pg_layout", "source"),
                "side_overrides": spec.get("side_overrides", {}),
            }
        )
    return records, build_specs


def load_pinned_sources(catalog_path, manifest_path):
    """Load the selected catalog records and build specs declared by a manifest."""
    manifest = read_manifest(manifest_path)
    catalog, catalog_path = _catalog(catalog_path)
    records, specs = _records(catalog, catalog_path, manifest["specs"])
    return records, specs, manifest, catalog_path


def build_from_manifest(catalog_path, manifest_path):
    """Return families, hints and a source/coverage report for one manifest."""
    records, specs, manifest, catalog_path = load_pinned_sources(catalog_path, manifest_path)
    families, hints = build_registry(records, specs)
    source_rows = [
        {
            "family_ref": family["family_id"],
            "signature": family["signature"],
            "slot_count": len(family["slots"]),
            "sources": [
                {
                    "family_id": alias["family_id"],
                    "library": (alias.get("source") or {}).get("lib"),
                    "cell": (alias.get("source") or {}).get("cell"),
                    "template_ref": (alias.get("source") or {}).get("template_ref"),
                }
                for alias in family.get("aliases", [])
            ],
        }
        for family in families
    ]
    source_rows.sort(key=lambda row: row["family_ref"])
    family_ids = {family["family_id"] for family in families}
    required = manifest["required_families"]
    report = {
        "format": REPORT_FORMAT,
        "catalog": str(catalog_path),
        "manifest": str(Path(manifest_path).resolve()),
        "families": source_rows,
        "coverage": {
            "family_count": len(families),
            "source_count": len(specs),
            "required_families": required,
            "missing_families": sorted(set(required) - family_ids),
        },
    }
    return families, hints, report


def _output(root):
    root = Path(root)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise TemplateError("symbol family output must be a new or empty directory")
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--catalog", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    output = _output(args.output)
    families, hints, report = build_from_manifest(args.catalog, args.manifest)
    write_registry(output, families, hints)
    loaded, loaded_hints = read_registry(output)
    expected = {family["family_id"]: family for family in families}
    if canonical(loaded) != canonical(expected) or loaded_hints != hints:
        raise TemplateError("written symbol family registry does not read back identically")
    report["output"] = str(output.resolve())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(canonical(report) + "\n")
    print(canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
