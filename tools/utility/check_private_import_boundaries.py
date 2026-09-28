"""Check declared source dependencies and frontend object contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

if __package__:
    from .a_philosophy_compat import validate_reference
    from .a_philosophy_exports import bindings
    from .boundary_objects import check_object_sources
    from .boundary_rules import internal_violations, violations
    from .boundary_symbols import source_module
else:
    from a_philosophy_compat import validate_reference
    from a_philosophy_exports import bindings
    from boundary_objects import check_object_sources
    from boundary_rules import internal_violations, violations
    from boundary_symbols import source_module

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = Path(__file__).with_name("private_import_boundaries.json")


def load_manifest(manifest=DEFAULT_MANIFEST, *, root=ROOT):
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported boundary manifest schema")
    result = {key: list(payload.get(key, [])) for key in ("rules", "internal", "objects", "compositions")}
    seen = set()
    for name in payload.get("includes", []):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name in seen:
            raise ValueError(f"invalid/duplicate boundary include: {name}")
        seen.add(name)
        part = json.loads((root / path).read_text(encoding="utf-8"))
        if part.get("schema_version") != 1 or part.get("includes"):
            raise ValueError(f"invalid boundary include: {name}")
        for key in result:
            result[key].extend(part.get(key, []))
    return result


def source_paths(rule, root=ROOT):
    if rule.get("paths"):
        return [root / name for name in rule["paths"]]
    directory = root / rule["root"]
    if not directory.is_dir():
        raise ValueError(f"missing boundary root: {rule['root']}")
    excluded = set(rule.get("exclude_files", []))
    paths = sorted(p for p in directory.rglob("*.py") if p.name not in excluded)
    if not paths:
        raise ValueError(f"empty boundary root: {rule['root']}")
    return paths


def check_rule(rule, *, root=ROOT):
    errors = []
    for path in source_paths(rule, root):
        name = path.relative_to(root).as_posix()
        try:
            found = violations(path.read_text(encoding="utf-8"), rule, path=name,
                               module=source_module(name, root), package=path.name == "__init__.py")
            errors.extend(f"{rule['id']}: {name}:{line}: {reason}" for line, reason in found)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{rule['id']}: {name}: AST parse failed: {exc}")
    return errors


def check_internal(contract, *, root=ROOT):
    import ast

    errors = []
    owner = root / contract["owner"]
    validate_reference(root, contract["owner"])
    if source_module(contract["owner"], root) != contract["module"]:
        raise ValueError(f"{contract['id']}: internal owner/module mismatch")
    available = bindings(ast.parse(owner.read_text(encoding="utf-8")))
    paths = source_paths(contract, root)
    names = {p.relative_to(root).as_posix() for p in paths}
    for consumer, symbols in contract["consumers"].items():
        validate_reference(root, consumer)
        if consumer not in names or consumer == contract["owner"]:
            errors.append(f"{contract['id']}: consumer outside declared scan: {consumer}")
        if not symbols or len(set(symbols)) != len(symbols):
            errors.append(f"{contract['id']}: empty/duplicate consumer symbols: {consumer}")
        for symbol in symbols:
            if symbol not in available:
                errors.append(f"{contract['id']}: missing internal owner symbol: {symbol}")
    for path in paths:
        name = path.relative_to(root).as_posix()
        if path == owner:
            continue
        try:
            found = internal_violations(path.read_text(encoding="utf-8"), contract, path=name,
                                        module=source_module(name, root), package=path.name == "__init__.py")
            errors.extend(f"{contract['id']}: {name}:{line}: {reason}" for line, reason in found)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{contract['id']}: {name}: AST parse failed: {exc}")
    return errors


def check(manifest=DEFAULT_MANIFEST, *, root=ROOT):
    payload = load_manifest(manifest, root=root)
    errors, seen = [], set()
    for key, entries in payload.items():
        for entry in entries:
            if entry["id"] in seen:
                errors.append("duplicate boundary id: " + entry["id"])
            seen.add(entry["id"])
            # Original F06 records predate explicit evidence; their scope remains.
            if key != "rules" or entry.get("evidence") is not None:
                if not entry.get("rationale") or not entry.get("evidence"):
                    errors.append(entry["id"] + ": boundary requires rationale/evidence")
                for ref in entry.get("evidence", []):
                    validate_reference(root, ref)
            for exception in entry.get("exceptions", []):
                if not exception.get("rationale"):
                    errors.append(entry["id"] + ": exception requires rationale")
                for path in exception.get("paths", []):
                    validate_reference(root, path)
            if key == "rules":
                errors.extend(check_rule(entry, root=root))
            elif key == "internal":
                errors.extend(check_internal(entry, root=root))
            elif key == "compositions":
                if not entry.get("ui_module") or not entry.get("backend_types"):
                    errors.append(entry["id"] + ": composition requires ui_module/backend_types")
                if not entry.get("rationale") or not entry.get("evidence"):
                    errors.append(entry["id"] + ": composition requires rationale/evidence")
                for ref in entry.get("evidence", []):
                    validate_reference(root, ref)
    errors.extend(check_object_sources(root, payload["objects"]))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    errors = check(args.manifest)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"declared source boundaries passed: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
