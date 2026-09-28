"""Run private source audit gates; does not compile or certify runtime payloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

if __package__:
    from .a_philosophy_baseline import check_baseline
    from .a_philosophy_compat import check_compatibility, compatibility_candidates, load_compatibility
    from .a_philosophy_reviews import review_candidates
    from .a_philosophy_inventory import check_inventories
    from .a_philosophy_metrics import python_metrics
    from .a_philosophy_sources import base_json, check_vendors, git, source_rows
    from .check_private_import_boundaries import check as check_private
    from .skill_load_audit import check_load_ownership
    from .skill_style import analyze as skill_style_findings
else:
    from a_philosophy_baseline import check_baseline
    from a_philosophy_compat import check_compatibility, compatibility_candidates, load_compatibility
    from a_philosophy_reviews import review_candidates
    from a_philosophy_inventory import check_inventories
    from a_philosophy_metrics import python_metrics
    from a_philosophy_sources import base_json, check_vendors, git, source_rows
    from check_private_import_boundaries import check as check_private
    from skill_load_audit import check_load_ownership
    from skill_style import analyze as skill_style_findings

ROOT = Path(__file__).resolve().parents[2]


def run(root=ROOT, *, base_ref="HEAD"):
    root = root.resolve()

    def load(name):
        return json.loads((root / "tools/utility" / name).read_text(encoding="utf-8"))

    # Validate the requested reference before reading a potentially absent manifest.
    base_commit = (
        git(root, "rev-parse", "--verify", base_ref + "^{commit}").decode().strip()
    )
    compatibility = load_compatibility(root)
    baseline = load("a_philosophy_size_baseline.json")
    inventories = load("a_philosophy_inventories.json")
    previous = base_json(
        root, base_ref, root / "tools/utility/a_philosophy_size_baseline.json"
    )
    rows, errors = source_rows(root)
    vendor_errors = check_vendors(root, rows, baseline)
    compat_errors = check_compatibility(root, compatibility)
    size_errors, warnings, hard = check_baseline(baseline, rows, previous)
    metrics, duplicates, parse_errors = python_metrics(root, rows)
    closure_errors, closures = check_inventories(root, inventories)
    private_errors = check_private(
        root / "tools/utility/private_import_boundaries.json", root=root
    )
    style_errors = [
        f"{row.path}:{finding.line}: {finding.kind}: {finding.name}"
        for row in rows if row.path.endswith((".il", ".ils", ".il.src"))
        for finding in skill_style_findings(root / row.path)
    ]
    load_errors, load_ownership = check_load_ownership(
        root, [row.path for row in rows], load("skill_load_ownership.json")
    )
    errors += (
        compat_errors
        + size_errors
        + parse_errors
        + closure_errors
        + private_errors
        + vendor_errors
        + style_errors
        + load_errors
    )
    candidates = compatibility_candidates(root, rows, compatibility["compatibility"])
    review_errors, review_warnings, candidates = review_candidates(
        root, candidates, compatibility["compatibility"], load("a_philosophy_reviews.json")
    )
    errors += review_errors
    warnings += review_warnings
    return {
        "schema_version": 1,
        "passed": not errors,
        "base_ref": base_ref,
        "base_commit": base_commit,
        "acceptance": "source-only; runtime compilation and release qualification not performed",
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "self_owned_files": sum(not row.vendored for row in rows),
            "vendored_files": sum(row.vendored for row in rows),
            "soft_over_300": sum(row.lines > 300 and not row.vendored for row in rows),
            "hard_over_500_self_owned": len(hard),
            "hard_over_500_tests": sum(row["test"] for row in hard),
            "hard_over_500_vendored": sum(
                row.vendored and row.lines > 500 for row in rows
            ),
            "python_parse_errors": len(parse_errors),
            "compatibility_records": len(compatibility["compatibility"]),
            "compatibility_candidates": len(candidates),
            "compatibility_reviewed": sum(c["review_state"] == "reviewed" for c in candidates),
        },
        "sources": [row.record() for row in rows],
        "hard_over_500_self_owned": hard,
        "vendored": [row.record() for row in rows if row.vendored],
        "python_metrics": metrics,
        "duplicate_candidates": duplicates,
        "compatibility_candidates": candidates,
        "entry_closure": closures,
        "skill_load_ownership": load_ownership,
        "checks": {
            "compatibility": compat_errors,
            "compatibility_reviews": review_errors,
            "size": size_errors,
            "ast": parse_errors,
            "inventory": closure_errors,
            "private_imports": private_errors,
            "vendor": vendor_errors,
            "skill_style": style_errors,
            "skill_load": load_errors,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--base-ref", default="HEAD", help="CI merge base for baseline ratchet"
    )
    parser.add_argument(
        "--output", type=Path, help="private JSON report, normally below .cad/"
    )
    args = parser.parse_args(argv)
    try:
        report = run(args.root, base_ref=args.base_ref)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"a-philosophy gate configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {key: report[key] for key in ("passed", "counts", "errors")},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
