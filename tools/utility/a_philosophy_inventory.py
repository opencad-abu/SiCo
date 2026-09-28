"""Resolve static local Python import closures against explicit native inputs."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

try:
    from .a_philosophy_sources import excluded, read_python
except ImportError:
    from a_philosophy_sources import excluded, read_python


def module_sources(root, search_paths):
    modules, errors = {}, []
    for search in search_paths:
        directory = root / search
        if not directory.is_dir():
            errors.append(f"missing Python search path: {search}")
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(directory)
            if excluded(relative) or {"test", "tests"} & set(relative.parts):
                continue
            if path.is_symlink():
                errors.append(f"local module symlink: {path.relative_to(root)}")
                continue
            name = ".".join(relative.with_suffix("").parts).removesuffix(".__init__")
            if not all(part.isidentifier() for part in name.split(".")):
                continue
            if name in modules and modules[name] != path:
                errors.append(f"ambiguous local module: {name}")
            modules[name] = path
    return modules, errors


def inventory_sources(root, value):
    path = root / value
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != "cad.python.native.v1" or not isinstance(
        data.get("modules"), list
    ):
        raise ValueError(f"unsupported native inventory: {value}")
    source_root = (path.parent / data["source_root"]).resolve()
    modules, errors, sources = {}, [], set()
    for item in data["modules"]:
        name = item["name"].removesuffix(".__init__")
        source = (source_root / item["source"]).resolve()
        if not all(part.isidentifier() for part in name.split(".")) or name in modules:
            errors.append(f"duplicate/invalid inventory module: {name}")
        if source in sources:
            errors.append(f"duplicate inventory source: {source}")
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            errors.append(f"missing/outside inventory source: {source}")
        if source.suffix != ".py" or {"tests", "reference", "skills"} & set(
            source.parts
        ):
            errors.append(f"non-library native input: {source}")
        modules[name] = source
        sources.add(source)
    return modules, errors


def dependencies(name, path, local):
    """Return modules, absent module imports, and nonliteral import clues.

    For ``from pkg import name``, name can be an attribute. Only append it
    when a module exists; the required pkg import still detects deleted
    modules. Missing attributes require behavioral tests, not an AST guess.
    """
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    tree = ast.parse(read_python(path), filename=str(path))
    required = {name.rpartition(".")[0]} - {""}
    candidates, dynamic = set(), []
    import_functions, import_modules = {"__import__"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "importlib"
            )
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "importlib"
            and node.level == 0
        ):
            import_functions.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "import_module"
            )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            required.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = importlib.util.resolve_name(
                "." * node.level + (node.module or ""), package
            )
            required.add(target)
            candidates.update(
                target + "." + alias.name for alias in node.names if alias.name != "*"
            )
        elif isinstance(node, ast.Call):
            function = node.func
            is_import = (
                isinstance(function, ast.Name) and function.id in import_functions
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr == "import_module"
                and isinstance(function.value, ast.Name)
                and function.value.id in import_modules
            )
            if not is_import:
                continue
            if (
                node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                value = node.args[0].value
                # Explicit package argument wins; __package__ uses lexical package.
                package_arg = next(
                    (kw.value for kw in node.keywords if kw.arg == "package"),
                    node.args[1] if len(node.args) > 1 else None,
                )
                actual_package = (
                    package_arg.value
                    if isinstance(package_arg, ast.Constant)
                    and isinstance(package_arg.value, str)
                    else package
                )
                required.add(importlib.util.resolve_name(value, actual_package))
            else:
                dynamic.append(
                    {"module": name, "line": node.lineno, "call": ast.unparse(node)}
                )
    tops = {key.split(".")[0] for key in local}
    missing = sorted(
        dep for dep in required if dep not in local and dep.split(".")[0] in tops
    )
    found = (required | candidates) & local.keys()
    for dep in tuple(found):
        parts = dep.split(".")
        found.update(
            ".".join(parts[:index])
            for index in range(1, len(parts))
            if ".".join(parts[:index]) in local
        )
    return found, missing, dynamic


def check_inventories(root: Path, payload):
    errors, reports = [], []
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return ["unsupported closure manifest schema"], []
    specs = payload.get("inventories")
    if not isinstance(specs, list) or not specs:
        return ["inventories must be a non-empty list"], []
    for spec in specs:
        if not isinstance(spec, dict):
            errors.append("inventory specification must be an object")
            continue
        try:
            if not isinstance(spec.get("path"), str) or not spec["path"].strip():
                raise ValueError("missing closure field: path")
            for field in ("search_paths", "entries"):
                values = spec.get(field)
                if (
                    not isinstance(values, list)
                    or not values
                    or not all(
                        isinstance(value, str) and value.strip() for value in values
                    )
                ):
                    raise ValueError(
                        f"closure field must be a non-empty string list: {field}"
                    )
            local, issues = module_sources(root, spec["search_paths"])
            inventory, inventory_issues = inventory_sources(root, spec["path"])
            issues.extend(inventory_issues)
            for name, source in inventory.items():
                if name not in local or local[name].resolve() != source:
                    issues.append(f"inventory name/source mismatch: {name}")
            pending, visited, dynamic = list(spec["entries"]), set(), []
            while pending:
                name = pending.pop()
                if name in visited:
                    continue
                visited.add(name)
                if name not in inventory:
                    issues.append(
                        f"reachable dependency missing from inventory: {name}"
                    )
                if name not in local:
                    issues.append(f"entry/dependency source missing: {name}")
                    continue
                deps, absent, clues = dependencies(name, local[name], local)
                issues.extend(
                    f"{name}: local import source missing: {dep}" for dep in absent
                )
                dynamic.extend(clues)
                pending.extend(sorted(deps - visited))
            for package in spec.get("complete_packages", []):
                for name in local:
                    if (
                        name == package or name.startswith(package + ".")
                    ) and name not in spec.get("development_entries", []):
                        if name not in inventory and name not in visited:
                            issues.append(
                                f"package source missing from inventory: {name}"
                            )
            reports.append(
                dict(
                    inventory=spec["path"],
                    entries=spec["entries"],
                    reachable=sorted(visited),
                    dynamic_import_review=dynamic,
                    errors=sorted(set(issues)),
                )
            )
            errors.extend(f"{spec['path']}: {issue}" for issue in sorted(set(issues)))
        except (
            OSError,
            ValueError,
            SyntaxError,
            KeyError,
            TypeError,
            ImportError,
        ) as exc:
            errors.append(
                f"{spec.get('path', 'inventory')}: closure check failed: {exc}"
            )
    return errors, reports
