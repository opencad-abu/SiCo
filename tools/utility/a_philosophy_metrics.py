"""Python AST review clues; structural metrics are not additional hard limits."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

try:
    from .a_philosophy_sources import read_python
except ImportError:  # Direct CLI execution from utility/.
    from a_philosophy_sources import read_python


class Metrics(ast.NodeVisitor):
    def __init__(self, path):
        self.path, self.scope, self.rows = path, [], []

    def record(self, node, kind, **values):
        self.rows.append(
            dict(
                path=self.path,
                kind=kind,
                line=node.lineno,
                lines=node.end_lineno - node.lineno + 1,
                **values,
            )
        )

    def visit_FunctionDef(self, node):
        kind = "method" if self.scope and self.scope[-1][0] == "class" else "function"
        name = ".".join([part[1] for part in self.scope] + [node.name])
        self.record(node, kind, name=name)
        self.scope.append(("function", node.name))
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        methods = [
            n
            for n in node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        attributes = {
            n.attr
            for m in methods
            for n in ast.walk(m)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Name)
            and n.value.id == "self"
            and isinstance(n.ctx, ast.Store)
        }
        self.record(
            node,
            "class",
            name=node.name,
            methods=len(methods),
            public_methods=sum(not n.name.startswith("_") for n in methods),
            annotated_fields=sum(isinstance(n, ast.AnnAssign) for n in node.body),
            assigned_attributes=sorted(attributes),
            bases=[ast.unparse(n) for n in node.bases],
        )
        self.scope.append(("class", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node):
        self.record(node, "import", module="", names=[n.name for n in node.names])

    def visit_ImportFrom(self, node):
        self.record(
            node,
            "import",
            module="." * node.level + (node.module or ""),
            names=[n.name for n in node.names],
            private=[n.name for n in node.names if n.name.startswith("_")],
        )


def python_metrics(root: Path, rows):
    metrics, errors, duplicate = [], [], defaultdict(list)
    for row in rows:
        if not row.path.endswith(".py") or row.vendored:
            continue
        try:
            tree = ast.parse(read_python(root / row.path), filename=row.path)
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{row.path}: AST parse failed: {exc}")
            continue
        visitor = Metrics(row.path)
        visitor.visit(tree)
        metrics.extend(visitor.rows)
        if row.test:
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in {"main", "build_parser"}:
                continue
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            key = (
                node.name,
                ast.dump(node.args),
                ast.dump(ast.Module(body=body, type_ignores=[])),
            )
            duplicate[key].append({"path": row.path, "line": node.lineno})
    candidates = [
        {"name": key[0], "locations": locations}
        for key, locations in duplicate.items()
        if len(locations) > 1
    ]
    return metrics, candidates, errors
