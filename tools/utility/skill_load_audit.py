"""Check source-load ownership and local version guards, without loading SKILL."""

from __future__ import annotations

import json
from pathlib import Path
import re

if __package__:
    from .skill_syntax import Frame, parse, tokenize
else:
    from skill_syntax import Frame, parse, tokenize

ROOT = Path(__file__).resolve().parents[2]
KINDS = {"worker", "runtime-init", "site-template"}


def _code(source):
    """Keep character offsets and string literals while removing comment contents."""
    return "".join(
        "".join("\n" if char == "\n" else " " for char in token.text)
        if token.kind == "comment" else token.text
        for token in tokenize(source)
    )


def local_guards(source):
    """Only guards with their own assignment protect definitions in that body.

    A dependency guard may compare another module's version without assigning
    it. It must not be mistaken for this file's guard (cadChangeTechLib forms).
    """
    code = _code(source)
    _, frames = parse(code)
    guards, errors = [], []
    for frame in frames:
        if frame.operator != "unless" or not frame.close_token:
            continue
        children = frame.children if frame.algebraic_name else frame.children[1:]
        if len(children) < 2 or not isinstance(children[1], Frame):
            continue
        condition = children[1]
        if condition.algebraic_name != "and" or not condition.close_token:
            continue
        predicate = code[condition.open_token.start:condition.close_token.end]
        body = code[condition.close_token.end:frame.close_token.start]
        for name in re.findall(r"boundp\(\s*'([\w]+Version)\s*\)", predicate):
            symbol = re.escape(name)
            assigned = re.findall(rf'\b{symbol}\s*=(?!=)\s*"([^"]*)"', body)
            assigned += re.findall(rf"set\(\s*'{symbol}\s+\"([^\"]*)\"\s*\)", body)
            if not assigned:
                continue  # A dependency guard, not a definition guard.
            compared = re.findall(
                rf"(?:\b{symbol}|symeval\(\s*'{symbol}\s*\))\s*==\s*\"([^\"]*)\"",
                predicate,
            )
            if len(compared) != 1 or assigned != compared:
                errors.append(f"{name}: compared {compared}, assigned {assigned}")
                continue
            guards.append((condition.close_token.end, frame.close_token.start, name))
    return guards, errors


def definition_positions(source):
    return [
        frame.open_token.start for frame in parse(source)[1]
        if frame.operator == "procedure" and not frame.quoted
    ]


def _scope(path):
    parts = Path(path).parts
    if parts[:2] == ("tools", "aivw"):
        return "independent-aivw"
    if {"test", "tests", "testing", "reference", "references"} & set(parts):
        return "fixture-or-reference"
    return "production"


def check_load_ownership(root, paths, manifest):
    """Require local guards or reviewed ownership for production .il functions.

    Explicit parent inventories are checked against loader source literals and
    a valid loader guard. Lifecycle records require concrete evidence anchors;
    these describe source applicability, not runtime build certification.
    """
    errors, records, ownership = [], [], {}
    if manifest.get("schema_version") != 1:
        return ["unsupported SKILL load-ownership schema"], []

    def read(path):
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"non-repository path: {path}")
        return (root / candidate).read_text(encoding="utf-8")

    def claim(path, kind, owner):
        if path in ownership:
            errors.append(f"duplicate SKILL load owner: {path}")
        ownership[path] = (kind, owner)

    for group in manifest.get("loaders", []):
        owner = group["owner"]
        try:
            source = read(owner)
            guards, invalid = local_guards(source)
            if not guards or invalid:
                errors.append(f"{owner}: invalid parent loader guard: {invalid}")
            literals = {token.text for token in tokenize(source) if token.kind == "string"}
            if not group.get("reason"):
                errors.append(f"{owner}: missing loader ownership rationale")
            for path in group["sources"]:
                name = Path(path).name
                if f'"{name}"' not in literals and f'"/skill/{name}"' not in literals:
                    errors.append(f"{path}: no source reference in loader {owner}")
                claim(path, "parent-loader", owner)
        except (OSError, ValueError) as exc:
            errors.append(f"{owner}: {exc}")

    for item in manifest.get("lifecycles", []):
        path, owner, kind = item["source"], item["owner"], item["kind"]
        claim(path, kind, owner)
        if kind not in KINDS or not item.get("reason") or not item.get("evidence"):
            errors.append(f"{path}: incomplete lifecycle ownership")
        for anchor in [{"path": owner, "contains": item["owner_anchor"]}, *item.get("evidence", [])]:
            try:
                if not anchor["contains"] or anchor["contains"] not in read(anchor["path"]):
                    errors.append(f"{path}: stale evidence anchor in {anchor['path']}")
            except (OSError, ValueError) as exc:
                errors.append(f"{path}: {exc}")

    used = set()
    for path in sorted(set(paths)):
        if not path.endswith(".il"):
            continue
        try:
            source = read(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        definitions = definition_positions(source)
        if not definitions:
            continue
        scope = _scope(path)
        if scope != "production":
            records.append(dict(path=path, kind=scope))
            continue
        guards, invalid = local_guards(source)
        errors.extend(f"{path}: {error}" for error in invalid)
        uncovered = [pos for pos in definitions if not any(start < pos < end for start, end, _ in guards)]
        if uncovered and path not in ownership:
            errors.append(f"{path}: {len(uncovered)} definitions lack a version guard or load owner")
        elif uncovered:
            kind, owner = ownership[path]
            used.add(path)
            records.append(dict(path=path, kind=kind, owner=owner))
        else:
            records.append(dict(path=path, kind="local-version-guard"))
    errors.extend(f"stale SKILL load ownership: {path}" for path in sorted(ownership.keys() - used))
    return errors, records


def check_repository(root=ROOT):
    if __package__:
        from .a_philosophy_sources import git
    else:
        from a_philosophy_sources import git
    paths = git(root, "ls-files", "*.il").decode().splitlines()
    manifest = json.loads((root / "tools/utility/skill_load_ownership.json").read_text())
    return check_load_ownership(root, paths, manifest)


if __name__ == "__main__":
    findings, ownership = check_repository()
    print(json.dumps(dict(errors=findings, ownership=ownership), indent=2))
    raise SystemExit(bool(findings))
