"""Acceptance checks for a staged standard builtin root before switching the tier.

The full staged root is read once to report identity, coverage, use/capability
distribution, mapping resolution and snapshot latency.  The reader API contract
(reference/investigate filters, detail paging, exact matching, retained-evidence
replay) is exercised on a small sample root so the acceptance stays bounded.
Nothing is written outside the report path.
"""

import argparse
import json
import time
from pathlib import Path

from cadai.template_catalog import TemplateCatalog
from cadai.template_standard import matching
from cadai.template_standard.reader import query, snapshot
from cadai.template_standard.replay import engine_record

PROFILES = (
    "topology-v1",
    "whole-reuse-v1",
    "core-reuse-v1",
    "placement-reference-v1",
    "routing-reference-v1",
    "symbol-reference-v1",
    "layout-reference-v1",
)


def tally(items, predicate):
    return sum(1 for item in items.values() if predicate(item))


def summary(item):
    entry = item["entry"]
    checks = item["documents"]["qualification"]["checks"]
    return dict(use=entry["use"], capabilities={k: v["result"] for k, v in checks.items()})


def distribution(rows, field):
    counts = {}
    for row in rows:
        value = row[field] if field in row else row["use"][field]
        counts[value] = counts.get(value, 0) + 1
    return counts


def full_report(root, mapping):
    catalog = TemplateCatalog(root=root)
    started = time.monotonic()
    items, revision = snapshot(catalog)
    elapsed = round(time.monotonic() - started, 1)
    rows = [summary(item) for item in items.values()]
    report = dict(
        staged=str(root),
        revision=revision,
        templates=len(items),
        snapshot_seconds=elapsed,
        detail_levels=distribution(
            [item["entry"] for item in items.values()], "detail_level"
        ),
        use_reference=distribution(rows, "reference"),
        use_reuse=distribution(rows, "reuse"),
        capabilities={},
    )
    for profile in PROFILES:
        results = [
            dict(value=row["capabilities"].get(profile, "absent")) for row in rows
        ]
        report["capabilities"][profile] = distribution(results, "value")
    report["reference_eligible"] = tally(
        items,
        lambda item: item["entry"]["use"]["reference"] == "allow"
        and item["documents"]["qualification"]["checks"]["topology-v1"]["result"] == "passed",
    )
    return report


def sample_report(root, legacy_root, mapping, sample):
    mapping = Path(mapping)
    catalog = TemplateCatalog(root=root)
    items, _ = snapshot(catalog)
    refs = sorted(items)
    report = dict(
        sample_root=str(root),
        templates=len(refs),
        investigate=query(catalog, dict(purpose="investigate", limit=100))["total"],
    )
    for purpose in ("reference", "reuse"):
        report["purpose_" + purpose] = query(
            catalog, dict(purpose=purpose, limit=100)
        )["total"]
    for profile in PROFILES:
        report["capability_" + profile] = query(
            catalog, dict(purpose="reference", capability=profile, limit=100)
        )["total"]
    detail, replay = {}, {}
    for ref in refs[:sample]:
        page = catalog.detail(
            dict(template_ref=ref, section="topology", entity="all", offset=0, limit=100)
        )
        detail[ref] = dict(
            sections=sorted(items[ref]["documents"]),
            entities=len(page["items"]),
            total=page["total"],
            complete=page["complete"],
        )
    for ref in refs[:sample]:
        try:
            record = engine_record(items[ref])
            replay[ref] = dict(ok=True, devices=len(record["topology"]["devices"]))
        except Exception as exc:
            replay[ref] = dict(ok=False, error=f"{type(exc).__name__}: {exc}")
    report["detail"] = detail
    report["replay"] = replay
    if len(refs) > 1:
        match = matching.exact(
            catalog,
            dict(
                template_ref=refs[0],
                target_ref=refs[1],
                port_map=None,
                omitted_groups=None,
            ),
        )
        report["exact_match"] = dict(left=refs[0], right=refs[1], ok=match["ok"])
    legacy = TemplateCatalog(root=legacy_root)
    pairs = (
        [json.loads(line) for line in mapping.read_text().splitlines() if line.strip()]
        if mapping and mapping.exists()
        else []
    )
    report["mapping"] = dict(
        rows=len(pairs),
        distinct_legacy=len({row["legacy_ref"] for row in pairs}),
        distinct_standard=len({row["template_ref"] for row in pairs}),
        unresolved=[
            row["legacy_ref"] for row in pairs if row["template_ref"] not in items
        ][:10],
    )
    report["legacy_refs"] = [
        dict(
            legacy_ref=row["legacy_ref"],
            cell=legacy.locate(row["legacy_ref"])[0]["source"]["cell"],
        )
        for row in pairs[:sample]
    ]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--sample-root", type=Path, required=True)
    parser.add_argument("--legacy-root", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=3)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = dict(
        full=full_report(args.root, args.mapping),
        sample=sample_report(
            args.sample_root, args.legacy_root, args.mapping, args.sample
        ),
    )
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.report:
        args.report.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    raise SystemExit(main())
