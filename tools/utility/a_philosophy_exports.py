"""Resolve explicit Python aliases and fingerprint reviewed module surfaces."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

if __package__:
    from .a_philosophy_sources import read_python
else:
    from a_philosophy_sources import read_python


def bindings(tree):
    """Only direct module bindings; conditional/dynamic exports require review."""
    result = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result[node.name] = node
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                result[alias.asname or alias.name.split(".")[0]] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                names = ast.walk(target) if isinstance(target, (ast.Tuple, ast.List)) else [target]
                for name in names:
                    if isinstance(name, ast.Name):
                        result[name.id] = node
    return result


def surface_digest(source):
    """Pin declarations, imports and export assignments, not function bodies.

    A new binding or changed signature invalidates review completion without
    declaring the new interface a violation. Comments/docstrings are excluded.
    """
    tree = ast.parse(source)
    surface = []
    for name, node in sorted(bindings(tree).items()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            value = (type(node).__name__, ast.dump(node.args),
                     [ast.dump(d) for d in node.decorator_list],
                     ast.dump(node.returns) if node.returns else None)
        elif isinstance(node, ast.ClassDef):
            value = ("class", [ast.dump(b) for b in node.bases],
                     surface_digest(ast.unparse(ast.Module(body=node.body, type_ignores=[]))))
        else:
            value = ast.dump(node)
        surface.append((name, value))
    # Conditional imports and declarations (e.g. Python-version adapters) also
    # belong to the reviewed surface; they must not silently evade its pin.
    surface.extend(("conditional", ast.dump(node)) for node in tree.body
                   if isinstance(node, (ast.If, ast.Try)))
    return hashlib.sha256(repr(surface).encode()).hexdigest()


class ExportResolver:
    """Follow explicit local imports and name aliases without importing code."""

    def __init__(self, root, search_paths):
        self.root = root
        self.search_paths = search_paths
        self.trees = {}

    def tree(self, path):
        if path not in self.trees:
            self.trees[path] = ast.parse(read_python(self.root / path), filename=path)
        return self.trees[path]

    def module_path(self, source, module, level=0):
        if level:
            parent = Path(source).parent
            for _ in range(level - 1):
                parent = parent.parent
            bases = [parent]
        else:
            bases = [Path(search) for search in self.search_paths]
        choices = []
        for base in bases:
            target = base.joinpath(*(module or "").split("."))
            for candidate in (target.with_suffix(".py"), target / "__init__.py"):
                if (self.root / candidate).is_file():
                    choices.append(candidate.as_posix())
        choices = sorted(set(choices))
        if len(choices) != 1:
            raise ValueError(f"missing/ambiguous export module {module!r} from {source}")
        return choices[0]

    def resolve(self, path, symbol, trail=()):
        key = (path, symbol)
        if key in trail:
            raise ValueError(f"cyclic compatibility alias: {path}:{symbol}")
        node = bindings(self.tree(path)).get(symbol)
        conditional = [n for n in self.tree(path).body if isinstance(n, (ast.If, ast.Try))]
        for control in conditional:
            for child in ast.walk(control):
                if isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    if any(isinstance(t, ast.Name) and t.id == symbol for t in targets) and node is not None:
                        raise ValueError(f"conditional export reassignment: {path}:{symbol}")
        if node is None:
            # Conditional literal diagnostics (e.g. selected TOML backend) are
            # owned here; never follow conditional imports as proven aliases.
            for control in conditional:
                for assignment in ast.walk(control):
                    if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant):
                        if any(isinstance(t, ast.Name) and t.id == symbol for t in assignment.targets):
                            return key
            raise ValueError(f"missing explicit export: {path}:{symbol}")
        trail = (*trail, key)
        if isinstance(node, ast.ImportFrom):
            alias = next(a for a in node.names if (a.asname or a.name) == symbol)
            owner = self.module_path(path, node.module, node.level)
            return self.resolve(owner, alias.name, trail)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if isinstance(value, ast.Name):
                return self.resolve(path, value.id, trail)
            if isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                imported = bindings(self.tree(path)).get(value.value.id)
                if isinstance(imported, ast.Import):
                    alias = next(a for a in imported.names if (a.asname or a.name) == value.value.id)
                    owner = self.module_path(path, alias.name)
                    return self.resolve(owner, value.attr, trail)
                if isinstance(imported, ast.ImportFrom):
                    alias = next(a for a in imported.names if (a.asname or a.name) == value.value.id)
                    module = ".".join(filter(None, (imported.module, alias.name)))
                    top = (imported.module or "").split(".")[0]
                    if not imported.level and not any(
                        (self.root / base / top).exists() or
                        (self.root / base / (top + ".py")).exists()
                        for base in self.search_paths
                    ):
                        return key  # Locally defined constant from an external API.
                    owner = self.module_path(path, module, imported.level)
                    return self.resolve(owner, value.attr, trail)
        if isinstance(node, ast.Import):
            raise ValueError(f"module export requires explicit symbol: {path}:{symbol}")
        return key


def check_export_groups(root, entries, search_paths):
    resolver, errors = ExportResolver(root, search_paths), []
    seen = set()
    for entry in entries:
        if "exports" not in entry:
            continue
        source, target = entry["source"], entry["replacement"]
        exports = entry["exports"]
        if not isinstance(exports, dict) or not exports:
            errors.append(f"{entry['legacy']}: exports must be a non-empty mapping")
            continue
        for name, replacement in exports.items():
            if not isinstance(name, str) or not isinstance(replacement, str):
                errors.append(f"{entry['legacy']}: export names must be strings")
                continue
            key = (source, name)
            if key in seen:
                errors.append(f"duplicate compatibility export: {source}:{name}")
            seen.add(key)
            try:
                actual = resolver.resolve(source, name)
                expected = resolver.resolve(target, replacement)
                if actual != expected:
                    errors.append(f"{source}:{name}: alias owner differs from {target}:{replacement}")
            except (OSError, ValueError, SyntaxError, KeyError, StopIteration) as exc:
                errors.append(f"{entry['legacy']}: {exc}")
    return errors
