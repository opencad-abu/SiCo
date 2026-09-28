"""Resolve direct Python references within lexical scopes for boundary checks."""

from __future__ import annotations

import ast
from pathlib import Path


def source_module(path, root):
    path = Path(path)
    parts = list(path.with_suffix("").parts)
    if "python" in parts:
        parts = parts[parts.index("python") + 1:]
    else:
        parent = path.parent
        while (root / parent / "__init__.py").is_file():
            parent = parent.parent
        parts = list(path.with_suffix("").relative_to(parent).parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def import_module(node, module, package=False):
    if not node.level:
        return node.module or ""
    parts = module.split(".") if package else module.split(".")[:-1]
    parts = parts[:len(parts) - node.level + 1]
    return ".".join(parts + ([node.module] if node.module else []))


def resolve(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, {node.id})
    if isinstance(node, ast.Attribute):
        return {name + "." + node.attr for name in resolve(node.value, aliases)}
    if isinstance(node, ast.Call):
        functions = resolve(node.func, aliases)
        if functions & {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            field = node.args[1]
            if isinstance(field, ast.Constant) and isinstance(field.value, str):
                return {name + "." + field.value for name in resolve(node.args[0], aliases)}
    return set()


def scope_nodes(body):
    for node in body:
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from scope_nodes(ast.iter_child_nodes(node))


def scope_aliases(body, inherited, module, package, arguments=None):
    nodes = list(scope_nodes(body))
    aliases = dict(inherited)
    local = {n.id for n in nodes if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    local.update(n.name for n in nodes
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
    if arguments:
        local.update(n.arg for n in ast.walk(arguments) if isinstance(n, ast.arg))
    for name in local:
        aliases[name] = {name}
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for item in node.names:
                if isinstance(node, ast.ImportFrom):
                    name = ".".join(filter(None, (import_module(node, module, package), item.name)))
                    local_name = item.asname or item.name
                else:
                    name = item.name if item.asname else item.name.split(".")[0]
                    local_name = item.asname or name
                aliases[local_name] = aliases.get(local_name, set()) | {name}
    # Monotone union preserves possible branch aliases, without mixing functions.
    # Direct alias chains converge in at most the number of local assignments.
    assignments = [n for n in nodes if isinstance(n, (ast.Assign, ast.AnnAssign))]
    for _ in range(len(assignments) + 1):
        changed = False
        for node in assignments:
            values = resolve(node.value, aliases)
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    previous = aliases.get(target.id, set())
                    merged = previous | values
                    if merged != previous:
                        aliases[target.id] = merged
                        changed = True
        if not changed:
            break
    return aliases


class References(ast.NodeVisitor):
    """Collect imports, references, calls and definitions; never execute source."""

    def __init__(self, tree, module="", package=False):
        self.module, self.package = module, package
        self.aliases = scope_aliases(tree.body, {}, module, package)
        self.enclosing = self.aliases
        self.items = []
        self.visit(tree)

    def record(self, node, kind, names):
        self.items.extend((node, kind, name) for name in sorted(names) if name)

    def visit_Import(self, node):
        self.record(node, "import", {item.name for item in node.names})

    def visit_ImportFrom(self, node):
        module = import_module(node, self.module, self.package)
        self.record(node, "import", {".".join(filter(None, (module, item.name)))
                                     for item in node.names})

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.record(node, "reference", resolve(node, self.aliases))

    def visit_Attribute(self, node):
        self.record(node, "reference", resolve(node, self.aliases))
        self.generic_visit(node)

    def visit_Call(self, node):
        self.record(node, "call", resolve(node.func, self.aliases))
        self.record(node, "reference", resolve(node, self.aliases))
        self.generic_visit(node)

    def function(self, node):
        self.record(node, "definition", {node.name})
        for value in (*node.decorator_list, *node.args.defaults, *node.args.kw_defaults):
            if value is not None:
                self.visit(value)
        self.nested(node.body, self.enclosing, node.args)

    visit_FunctionDef = function
    visit_AsyncFunctionDef = function

    def visit_ClassDef(self, node):
        self.record(node, "definition", {node.name})
        for value in (*node.bases, *node.decorator_list):
            self.visit(value)
        self.nested(node.body, self.aliases, class_scope=True)

    def visit_Lambda(self, node):
        self.nested([node.body], self.enclosing, node.args)

    def nested(self, body, inherited, arguments=None, class_scope=False):
        previous, enclosing = self.aliases, self.enclosing
        self.aliases = scope_aliases(body, inherited, self.module, self.package, arguments)
        if not class_scope:
            self.enclosing = self.aliases
        for node in body:
            self.visit(node)
        self.aliases, self.enclosing = previous, enclosing
