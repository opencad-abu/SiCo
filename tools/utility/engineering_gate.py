"""One offline source-quality gate: strict pass, accepted debt, failure, or error."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from a_philosophy_sources import base_json, excluded, git, source_file
from engineering_debt import assess
from engineering_bootstrap import verify as verify_initial_debt
from engineering_findings import a_findings, mcp_findings
from mcp_catalog_history import check_history

ROOT = Path(__file__).resolve().parents[2]
DEBT = "tools/utility/engineering_debt.json"
BUDGET = "tools/utility/mcp_catalog_budget.json"


def evaluate(root, a_report, catalog, budget, mcp_result, *, base_commit, today=None):
    """Evaluate fresh reports; no cached report is accepted by the public CLI."""
    payload = json.loads((root / DEBT).read_text())
    previous = base_json(root, base_commit, root / DEBT)
    findings = a_findings(root, a_report) + mcp_findings(catalog, budget, mcp_result)
    debt = assess(findings, payload, previous=previous, base_commit=base_commit, today=today)
    history_errors = check_history(budget, base_json(root, base_commit, root / BUDGET))
    errors = debt["errors"] + history_errors
    state = "failed" if errors else "accepted_debt" if findings else "passed"
    stages = {}
    accepted = {row["id"] for row in debt["accepted_debt"]}
    reviews_passed = not any(row["gate"] == "reviews" for row in findings)
    for name, strict in (("a", a_report["passed"]), ("mcp", mcp_result["passed"]), ("reviews", reviews_passed)):
        rows = [row for row in findings if row["gate"] == name]
        stage = "failed" if any(row["id"] not in accepted for row in rows) else "accepted_debt" if rows else "passed"
        stages[name] = dict(raw_passed=strict, findings=len(rows), status=stage)
    if errors:
        stages["governance"] = dict(status="failed", errors=errors)
    else:
        stages["governance"] = dict(status="passed")
    return dict(schema_version=1, status=state, passed=state == "passed", eligible=not errors,
                evaluated_on=(today or date.today()).isoformat(), base_commit=base_commit,
                errors=errors, stages=stages, debt=debt,
                counts=dict(findings=len(findings), accepted_debt=len(debt["accepted_debt"]),
                            unregistered=len(debt["unregistered"]), resolved=len(debt["resolved"])),
                not_run={"product_regressions": "Run the affected source suites separately.",
                         "gui_eda": "Requires explicit environment and opt-in probes.",
                         "runtime_release": "Source gate does not qualify native/context artifacts or archives."})


def source_identity(root):
    paths = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    digests = {}
    for name in sorted(set(paths) - {""}):
        path = Path(name)
        hook = path.parts[0] in {".gitea", ".githooks"}
        if not hook and (excluded(path) or not (
                source_file(path) or path.suffix in {".json", ".toml", ".yml", ".yaml", ".md"})):
            continue
        target = root / path
        digests[name] = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
    return hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()


def run(root, base_ref, output):
    root, output = root.resolve(), output.resolve()
    base_commit = git(root, "rev-parse", "--verify", base_ref + "^{commit}").decode().strip()
    git(root, "merge-base", "--is-ancestor", base_commit, "HEAD")
    output.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="run-", dir=output))
    head = git(root, "rev-parse", "HEAD").decode().strip()
    before = source_identity(root)
    commands = (
        ("a", ["a_philosophy_gate.py", "--root", str(root), "--base-ref", base_commit,
               "--output", str(output / "a.json")]),
        ("catalog", ["mcp_catalog_report.py", "--output", str(output / "catalog.json")]),
        ("mcp", ["mcp_catalog_budget.py", "--report", str(output / "catalog.json"), "--base-ref", base_commit]),
    )
    for name, args in commands:
        command = [sys.executable, "-B", "-s", str(root / "tools/utility" / args[0]), *args[1:]]
        result = subprocess.run(command, cwd=root, capture_output=True, text=True)
        (output / (name + ".stdout")).write_text(result.stdout)
        (output / (name + ".stderr")).write_text(result.stderr)
        if result.returncode not in ({0, 1} if name in {"a", "mcp"} else {0}):
            raise ValueError(f"{name} check could not execute (exit {result.returncode}); see {output}")
    a_report = json.loads((output / "a.json").read_text())
    catalog = json.loads((output / "catalog.json").read_text())
    mcp_result = json.loads((output / "mcp.stdout").read_text())
    budget = json.loads((root / BUDGET).read_text())
    result = evaluate(root, a_report, catalog, budget, mcp_result, base_commit=base_commit)
    if base_json(root, base_commit, root / DEBT) is None:
        payload = json.loads((root / DEBT).read_text())
        initial_errors = verify_initial_debt(root, base_commit, payload, output / "initial-debt")
        result["initial_debt"] = dict(errors=initial_errors, evidence=str(output / "initial-debt"))
        if initial_errors:
            result.update(status="failed", passed=False, eligible=False)
            result["errors"].extend(initial_errors)
    if source_identity(root) != before or git(root, "rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("source/configuration changed during checks; run again on a stable checkout")
    result["source"] = dict(commit=head,
                            dirty=bool(git(root, "status", "--porcelain")), root=str(root),
                            inputs_sha256=before)
    result["reports"] = {name: {"path": str(output / name),
                               "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
                         for name in ("a.json", "catalog.json", "mcp.stdout")}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", required=True, help="Target branch/commit before this change")
    parser.add_argument("--output", type=Path, default=ROOT / ".cad/engineering-gate")
    args = parser.parse_args(argv)
    try:
        report = run(ROOT, args.base_ref, args.output)
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as exc:
        report = dict(status="error", passed=False, eligible=False, errors=[str(exc)])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("status", "passed", "eligible", "errors")},
                     ensure_ascii=False, indent=2))
    return 2 if report["status"] == "error" else 0 if report["eligible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
