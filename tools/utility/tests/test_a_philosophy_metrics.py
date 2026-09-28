"""AST metrics include methods, async functions, fields, and multiline imports."""

from utility.a_philosophy_metrics import python_metrics
from utility.a_philosophy_sources import source_rows
from utility.check_private_import_boundaries import check
from conftest import write
import json


def test_metrics_retain_method_context_and_multiline_imports(repo):
    write(
        repo,
        "sample.py",
        """from owner import (
    _private,
    public,
)
class Outer:
    field: str
    async def run(self):
        self.state = 1
        def nested():
            return 2
        return nested()
def top():
    return 3
""",
    )
    rows, _ = source_rows(repo)
    metrics, _, errors = python_metrics(repo, rows)
    assert not errors
    by_name = {item.get("name"): item for item in metrics}
    assert by_name["Outer.run"]["kind"] == "method"
    assert by_name["Outer.run.nested"]["kind"] == "function"
    assert by_name["Outer"]["annotated_fields"] == 1
    assert by_name["Outer"]["assigned_attributes"] == ["state"]
    assert by_name["top"]["kind"] == "function"
    assert metrics[0]["lines"] == 4
    assert metrics[0]["private"] == ["_private"]


def test_parse_errors_are_hard_but_duplicates_are_candidates(repo):
    write(repo, "a.py", "def digest(value):\n    return value\n")
    write(repo, "b.py", "def digest(value):\n    return value\n")
    write(repo, "tests/bad.py", "def broken(:\n")
    rows, _ = source_rows(repo)
    _, duplicates, errors = python_metrics(repo, rows)
    assert len(errors) == 1 and "AST parse failed" in errors[0]
    assert duplicates[0]["name"] == "digest"


def test_private_gate_multiline_and_parse_failure(repo):
    write(repo, "pkg/consumer.py", "from .owner import (\n    _private,\n)\n")
    manifest = write(
        repo,
        "boundaries.json",
        json.dumps(
            dict(
                schema_version=1,
                rules=[
                    dict(id="owner", root="pkg", forbid_private_imports_from=["owner"])
                ],
            )
        ),
    )
    errors = check(manifest, root=repo)
    assert len(errors) == 1 and "private import" in errors[0]
    write(repo, "pkg/consumer.py", "def broken(:\n")
    errors = check(manifest, root=repo)
    assert len(errors) == 1 and "AST parse failed" in errors[0]
