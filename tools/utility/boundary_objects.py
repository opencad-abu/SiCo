"""Check declared frontend products statically and on composed Python objects."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass


def class_surface(node):
    names = set()
    for member in node.body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(member.name)
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            names.add(member.target.id)
        if isinstance(member, ast.Assign):
            names.update(target.id for target in member.targets if isinstance(target, ast.Name))
    for member in ast.walk(node):
        if (isinstance(member, ast.Attribute) and isinstance(member.ctx, ast.Store)
                and isinstance(member.value, ast.Name) and member.value.id == "self"):
            names.add(member.attr)
    return {name for name in names if not name.startswith("_")}


def check_object_sources(root, contracts):
    errors = []
    for contract in contracts:
        path, _, name = contract["source"].partition(":")
        try:
            tree = ast.parse((root / path).read_text(encoding="utf-8"))
            node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
            actual = class_surface(node)
            expected = set(contract["public"])
            if actual != expected:
                errors.append(f"{name}: public surface differs: added={sorted(actual - expected)}, "
                              f"missing={sorted(expected - actual)}")
            if node.bases:
                errors.append(f"{name}: undeclared base/mixin requires composed-object review")
            decorators = [ast.unparse(d) for d in node.decorator_list]
            expected_decorators = ["dataclass(frozen=True)"] if contract.get("frozen") else []
            if decorators != expected_decorators:
                errors.append(f"{name}: undeclared decorator or missing frozen dataclass")
            if "fields" in contract:
                actual_fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)
                                 and isinstance(n.target, ast.Name)]
                if actual_fields != contract["fields"]:
                    errors.append(f"{name}: data fields differ from contract")
        except (OSError, SyntaxError, StopIteration) as exc:
            errors.append(f"{contract['source']}: invalid object source: {exc}")
    return errors


def object_errors(value, contract):
    """Inspect the actual inherited/decorated surface without evaluating properties."""
    names = {name for name in dir(value) if not name.startswith("_")}
    expected = set(contract["public"])
    errors = []
    if names != expected:
        errors.append(f"public surface differs: added={sorted(names - expected)}, "
                      f"missing={sorted(expected - names)}")
    if contract.get("frozen"):
        if not is_dataclass(value) or not value.__dataclass_params__.frozen:
            errors.append("expected frozen dataclass")
        elif [field.name for field in fields(value)] != contract["fields"]:
            errors.append("data fields differ from contract")
        elif set(vars(value)) != set(contract["fields"]):
            errors.append("undeclared instance storage on data value")
    for name in contract.get("forbid_attributes", []):
        if name in dir(value):
            errors.append("backend escape " + name)
    return errors


def composition_errors(value, contract):
    """Walk UI-owned collaborators and containers; registered ports remain opaque.

    This inspects stored objects, including private UI fields. It does not call
    properties, inspect closures, or claim a proof about arbitrary Python code.
    """
    errors, seen, pending = [], set(), [("window", value)]
    for name in contract["forbid_attributes"]:
        if name in dir(value):
            errors.append("composed window exposes " + name)
    while pending:
        location, current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        bases = type(current).__mro__
        if any(base.__name__ in contract["backend_types"]
               or base.__module__ + "." + base.__name__ in contract["backend_types"]
               for base in bases):
            errors.append(location + ": backend object " + type(current).__name__)
            continue
        if isinstance(current, dict):
            pending.extend((location + "[value]", item) for item in current.values())
        elif isinstance(current, (list, tuple, set, frozenset)):
            pending.extend((location + "[]", item) for item in current)
        elif any(base.__module__.startswith(contract["ui_module"] + ".") for base in bases):
            pending.extend((location + "." + name, item) for name, item in vars(current).items())
    return errors
