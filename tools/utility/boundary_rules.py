"""Evaluate declared dependency and operation boundaries on Python references."""

from __future__ import annotations

import ast

if __package__:
    from .boundary_symbols import References
else:
    from boundary_symbols import References


def within(name, prefix):
    return name == prefix or name.startswith(prefix + ".")


def permitted(rule, path, name, kind, node):
    for item in rule.get("exceptions", []):
        if item["kind"] != kind or item["symbol"] != name:
            continue
        if item.get("paths") and path not in item["paths"]:
            continue
        if kind == "call":
            if "any_args" in item:
                if (len(node.args) == item["any_args"]
                        and sorted(keyword.arg or "" for keyword in node.keywords)
                        == sorted(item.get("keywords", []))):
                    return True
                continue
            if node.keywords or len(node.args) != len(item["args"]):
                continue
            if any(not isinstance(arg, ast.Constant) or type(arg.value) is not type(value)
                   or arg.value != value for arg, value in zip(node.args, item["args"])):
                continue
        return True
    return False


def violations(source, rule, *, path="", module="", package=False):
    """Return line/reason pairs for one declared rule and source file."""
    tree = ast.parse(source, filename=path or "<boundary>")
    found = set()
    for node, kind, name in References(tree, module, package).items:
        parts = name.split(".")
        leaf = parts[-1]
        reason = ""
        if kind == "import":
            if any(within(name, p) for p in rule.get("forbid_modules", [])):
                reason = "forbidden import " + name
            if any(name.startswith(p) for p in rule.get("forbid_module_prefixes", [])):
                reason = "forbidden import " + name
        if kind != "definition" and leaf in rule.get("forbid_symbols", []):
            reason = "backend reference " + name
        if kind == "definition" and leaf in rule.get("forbid_definitions", []):
            reason = "forbidden definition " + name
        if kind == "reference":
            if set(parts[1:]) & set(rule.get("forbid_attributes", [])):
                reason = "backend attribute " + name
            if any(part.startswith(prefix) for part in parts[1:]
                   for prefix in rule.get("forbid_attribute_prefixes", [])):
                reason = "backend storage " + name
        for owner in rule.get("forbid_private_imports_from", []):
            for index, part in enumerate(parts[1:], 1):
                parent = ".".join(parts[:index])
                if (parent == owner or parent.endswith("." + owner)) and (part.startswith("_") or part == "*"):
                    reason = "private import/reference " + name
        if any(name == value or name.endswith("." + value)
               for value in rule.get("forbid_private_attributes", [])):
            reason = "private attribute " + name
        if kind == "call":
            if leaf in rule.get("forbid_calls", []) or name in rule.get("forbid_exact_calls", []):
                reason = "forbidden call " + name
            if leaf == "result" and rule.get("forbid_result_calls"):
                reason = "unscoped future result " + name
            if len(parts) > 1 and parts[-2].endswith("controller"):
                if leaf in rule.get("forbid_controller_calls", []):
                    reason = "controller command " + name
        if reason and not (permitted(rule, path, name, kind, node)
                           or permitted(rule, path, leaf, kind, node)):
            found.add((node.lineno, reason))
    return sorted(found)


def internal_violations(source, contract, *, path, module, package=False):
    """Only registered consumers may use a declared internal module's symbols."""
    allowed = contract["consumers"].get(path, [])
    prefix = contract["module"] + "."
    found = set()
    for node, kind, name in References(ast.parse(source), module, package).items:
        if kind == "definition" or not name.startswith(prefix):
            continue
        symbol = name[len(prefix):].split(".")[0]
        if symbol not in allowed:
            found.add((node.lineno, "undeclared internal access " + name))
    return sorted(found)
