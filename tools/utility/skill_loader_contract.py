"""Read source-load membership and derive per-file contracts from SKILL forms."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

if __package__:
    from .skill_syntax import Frame, Token, parse, unbalanced_tokens
else:
    from skill_syntax import Frame, Token, parse, unbalanced_tokens

MANIFEST = Path("tools/utility/skill_loader/manifest.json")
KERNEL = Path("scripts/sicoSkillLoader.ils")


def repository_path(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value:
        raise ValueError(f"invalid source path: {value}")
    return path


def members(root, group):
    paths = []
    if group.get("source_manifest"):
        document = json.loads((root / repository_path(group["source_manifest"])).read_text())
        paths = [str(Path(group.get("source_directory", "")) / name)
                 for name in document["sources"]]
    return paths + group["sources"]


def manifest(root):
    data = json.loads((root / MANIFEST).read_text())
    if data.get("schema_version") != 1 or not data.get("groups"):
        raise ValueError("unsupported source loader manifest")
    groups, owners, outputs = data["groups"], {}, set()
    for name, group in groups.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise ValueError(f"invalid group: {name}")
        output = str(repository_path(group["output"]))
        if output in outputs:
            raise ValueError(f"duplicate source entry: {output}")
        outputs.add(output)
        for source in members(root, group):
            path = repository_path(source)
            if path.suffix not in {".il", ".ils"} or not (root / path).is_file():
                raise ValueError(f"missing SKILL source: {path}")
            if source in owners:
                raise ValueError(f"duplicate source owner: {source}")
            owners[source] = name
    discovered = {str(path.relative_to(root)) for directory in data.get("source_directories", [])
                  for path in (root / repository_path(directory)).glob("*")
                  if path.suffix in {".il", ".ils"}}
    excluded = data.get("excluded", {})
    if any(not reason for reason in excluded.values()):
        raise ValueError("source exclusions need a lifecycle reason")
    missing = discovered - owners.keys() - outputs - excluded.keys()
    stale = excluded.keys() - discovered
    if missing or stale:
        raise ValueError(f"unowned SKILL sources: {sorted(missing)}; stale exclusions: {sorted(stale)}")
    procedures = {}
    for source in owners:
        for frame in parse((root / source).read_text())[1]:
            if frame.operator == "procedure" and not frame.quoted:
                signature = frame.children[1]
                name = signature.algebraic_name
                if name in procedures:
                    raise ValueError(f"duplicate procedure owner: {name}: {procedures[name]}, {source}")
                procedures[name] = source
    for name in groups:
        dependencies(groups, name)
    if outputs.intersection(owners):
        raise ValueError("source entries cannot be implementation members")
    return groups


def dependencies(groups, name, stack=()):
    if name in stack:
        raise ValueError(f"source dependency cycle: {' -> '.join((*stack, name))}")
    if name not in groups:
        raise ValueError(f"unknown source dependency: {name}")
    ordered = []
    for dependency in groups[name]["requires"]:
        ordered.extend(dependencies(groups, dependency, (*stack, name)))
    return list(dict.fromkeys([*ordered, name]))


def declarations(source):
    """Capture callable definitions, literal revisions and read-side guards.

    Variable-returning Revision helpers are checked by identity/callability;
    literal revisions additionally receive an exact post-load check. Methods
    are represented by their generic function, classes by findClass checks.
    """
    if unbalanced_tokens(source):
        raise ValueError("unbalanced SKILL source")
    exports, revisions, guards = [], [], []
    frames = parse(source)[1]
    for frame in frames:
        if frame.quoted:
            continue
        if frame.operator in {"defmacro", "defgeneric", "defmethod"}:
            name = frame.children[0].text
            if name not in exports:
                exports.append(name)
            continue
        if frame.operator != "procedure":
            continue
        signature = frame.children[1]
        if not isinstance(signature, Frame) or not signature.algebraic_name:
            raise ValueError("expected algebraic procedure definition")
        name = signature.algebraic_name
        if name in exports:
            raise ValueError(f"duplicate procedure: {name}")
        exports.append(name)
        if (name.endswith("Revision") and not signature.children
                and len(frame.children) == 3
                and isinstance(frame.children[2], Token)
                and frame.children[2].kind == "string"):
            revisions.append((name, json.loads(frame.children[2].text)))
    positions = [frame.open_token.start for frame in frames
                 if frame.operator in {"procedure", "defclass", "defmacro", "defgeneric", "defmethod"}
                 and not frame.quoted]
    for frame in frames:
        if frame.operator != "unless" or frame.quoted or len(frame.children) < 2:
            continue
        condition = frame.children[1]
        if not isinstance(condition, Frame) or not condition.close_token:
            continue
        if not any(condition.close_token.end < pos < frame.close_token.start for pos in positions):
            continue
        code = source[condition.open_token.start:condition.close_token.end]
        guards.extend(re.findall(r"boundp\(\s*'(\w+Version)\s*\)", code))
    guards = list(dict.fromkeys(guards))
    return exports, revisions, guards


def contract(root, group):
    rows = []
    for path in members(root, group):
        source = (root / path).read_text()
        exports, revisions, guards = declarations(source)
        rows.append((path, hashlib.sha256(source.encode()).hexdigest(),
                     exports, revisions, guards))
    fingerprint = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return fingerprint, rows


def classes(source):
    return [frame.children[0].text for frame in parse(source)[1]
            if frame.operator == "defclass" and not frame.quoted]
