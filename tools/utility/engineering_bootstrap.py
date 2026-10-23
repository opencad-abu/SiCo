"""Prove the first debt registry contains only findings already in its base."""

from __future__ import annotations

import json
import subprocess
import sys

from engineering_findings import a_findings, mcp_findings


def compare_initial(entries, findings):
    original = {row["id"]: row for row in findings}
    errors = []
    for entry in entries:
        before = original.get(entry["id"])
        if before is None:
            errors.append(f"initial allowance is not a base finding: {entry['id']}")
        elif "value" in before:
            if type(entry.get("ceiling")) is not int or entry["ceiling"] > before["value"]:
                errors.append(f"initial allowance grows beyond the base: {entry['id']}")
        elif entry.get("fingerprint") != before["fingerprint"]:
            errors.append(f"initial allowance changes base evidence: {entry['id']}")
    return errors


def verify(root, commit, payload, output):
    """Run the base's original analyzers in a separate checkout; retain receipts."""
    checkout = output / "base-checkout"
    output.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(root), "worktree", "add", "--detach", "--quiet",
                    str(checkout), commit], check=True)
    try:
        commands = (
            ("a", ["a_philosophy_gate.py", "--base-ref", commit, "--output", str(output / "a.json")]),
            ("catalog", ["mcp_catalog_report.py", "--output", str(output / "catalog.json")]),
            ("mcp", ["mcp_catalog_budget.py", "--report", str(output / "catalog.json")]),
        )
        for name, args in commands:
            result = subprocess.run([sys.executable, "-B", "-s",
                                     str(checkout / "tools/utility" / args[0]), *args[1:]],
                                    cwd=checkout, capture_output=True, text=True)
            (output / (name + ".stdout")).write_text(result.stdout)
            (output / (name + ".stderr")).write_text(result.stderr)
            if result.returncode not in ({0, 1} if name in {"a", "mcp"} else {0}):
                raise ValueError(f"initial debt comparison could not run base {name}")
        a_report = json.loads((output / "a.json").read_text())
        catalog = json.loads((output / "catalog.json").read_text())
        budget = json.loads((checkout / "tools/utility/mcp_catalog_budget.json").read_text())
        result = json.loads((output / "mcp.stdout").read_text())
        findings = a_findings(checkout, a_report) + mcp_findings(catalog, budget, result)
        errors = compare_initial(payload["entries"], findings)
        (output / "comparison.json").write_text(json.dumps(dict(base_commit=commit, errors=errors), indent=2) + "\n")
        return errors
    finally:
        subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(checkout)], check=True)
