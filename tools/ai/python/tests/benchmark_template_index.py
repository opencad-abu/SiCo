"""Reproducible offline P3 index/full-search benchmark.

This is an evidence runner, not a production hot path.  It generates a fixed
structural corpus by repeating six topology families with unique immutable
references, then measures the necessary-condition scan and the authoritative
full exact matcher.  The JSON output is suitable for private evidence and is
never written under a template catalog or runtime package.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from replay_template_baseline import source_identity  # noqa: E402
from template_search_corpus import FAMILIES, VERSION, pool_item, topology  # noqa: E402

from cadai.template_index import build_index, necessary_candidates  # noqa: E402
from cadai.template_schema import canonical, digest  # noqa: E402
from cadai.template_search import MATCHER_VERSION, search_exact  # noqa: E402

SIZES = (40, 1000, 10000)


def percentile(samples, fraction):
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def corpus(size):
    families = tuple(FAMILIES)
    return [
        pool_item(topology(families[index % len(families)]), number=index)
        for index in range(size)
    ]


def index_scan(index, target):
    kinds = {}
    for device in target["devices"]:
        kinds[device["kind"]] = kinds.get(device["kind"], 0) + 1
    started = time.perf_counter()
    refs = necessary_candidates(
        index,
        device_kinds=kinds,
        min_devices=len(target["devices"]),
        max_devices=len(target["devices"]),
    )
    return refs, (time.perf_counter() - started) * 1000


def full_search(target, records):
    started = time.perf_counter()
    result = search_exact(
        target,
        records,
        {"max_query_time_ms": 10000, "max_match_states": 200000},
    )
    elapsed = (time.perf_counter() - started) * 1000
    return result, elapsed


def run(sizes=SIZES, *, index_root=None):
    target = topology("inverter")
    rows = []
    for size in sizes:
        records = corpus(size)
        path = Path(index_root or "/tmp") / ("template-index-benchmark-" + str(size) + ".json")
        snapshot = {
            "snapshot_ref": "snp_benchmark_" + digest({"size": size, "families": list(FAMILIES)}),
            "matcher_version": MATCHER_VERSION,
        }
        started = time.perf_counter()
        index = build_index(snapshot, records, path)
        build_ms = (time.perf_counter() - started) * 1000
        scan_samples, full_samples = [], []
        indexed_refs, full = None, None
        # One measured run is enough for the expensive full matcher at 10k;
        # five runs stabilize the inexpensive necessary-condition scan.
        scan_count = 5 if size < 10000 else 3
        for _ in range(scan_count):
            indexed_refs, scan_ms = index_scan(index, target)
            scan_samples.append(scan_ms)
        full, full_ms = full_search(target, records)
        full_samples.append(full_ms)
        selected_refs = set(indexed_refs or [])
        indexed_records = [row for row in records if row["template_ref"] in selected_refs]
        indexed_result, indexed_ms = full_search(target, indexed_records)
        # ``search_exact`` checks the pool in deterministic ref order.  The
        # benchmark stores the examined prefix only as a diagnostic digest;
        # it is not used as a recall claim when the run is inconclusive.
        full_status_refs = {
            row["template_ref"] for row in sorted(records, key=lambda row: row["template_ref"])[
                : full["search"]["examined"]
            ]
        }
        rows.append(
            {
                "candidates": size,
                "index_rows": len(index["rows"]),
                "indexed_candidates": len(indexed_refs or []),
                "full_examined": full["search"]["examined"],
                "full_remaining": full["search"]["remaining"],
                "full_status": full["status"],
                "full_complete": full["search"]["complete"],
                "full_stop_reason": full["search"]["stop_reason"],
                "full_unresolved": full["diagnostics"]["unresolved"],
                "full_states_used": full["diagnostics"]["states_used"],
                "indexed_status": indexed_result["status"],
                "indexed_complete": indexed_result["search"]["complete"],
                "indexed_examined": indexed_result["search"]["examined"],
                "indexed_remaining": indexed_result["search"]["remaining"],
                "indexed_stop_reason": indexed_result["search"]["stop_reason"],
                "indexed_unresolved": indexed_result["diagnostics"]["unresolved"],
                "decision_equal": (
                    all(indexed_result[key] == full[key]
                        for key in ("status", "best_match", "alternatives"))
                    if full["search"]["complete"] and indexed_result["search"]["complete"]
                    else None
                ),
                "indexed_ref_digest": digest(sorted(indexed_refs or [])),
                "full_examined_ref_digest": digest(sorted(full_status_refs)),
                "build_ms": build_ms,
                "index_scan_ms": {
                    "p50": percentile(scan_samples, 0.50),
                    "p95": percentile(scan_samples, 0.95),
                    "p99": percentile(scan_samples, 0.99),
                },
                "full_search_ms": {
                    "samples": len(full_samples),
                    "p50": percentile(full_samples, 0.50),
                    "p95": percentile(full_samples, 0.95),
                    "p99": percentile(full_samples, 0.99),
                },
                "indexed_search_ms": indexed_ms,
            }
        )
        path.unlink(missing_ok=True)
    return {
        "schema": "cad.template.index-benchmark.v1",
        "dataset": {
            "corpus_version": VERSION,
            "families": list(FAMILIES),
            "sizes": list(sizes),
            "target_family": "inverter",
            "records_digest": digest(
                {size: [row["template_ref"] for row in corpus(size)] for size in sizes}
            ),
        },
        "source": {
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "source_tree": source_identity(),
            "python_hash_seed": os.environ.get("PYTHONHASHSEED", "random"),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpus": os.cpu_count(),
            "matcher": MATCHER_VERSION,
            "cache": "disabled",
            "storage": "in_memory_generated",
        },
        "results": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes", nargs="*", type=int, default=list(SIZES))
    args = parser.parse_args(argv)
    if not args.sizes or any(size not in SIZES for size in args.sizes):
        parser.error("sizes must be selected from 40, 1000, 10000")
    report = run(tuple(args.sizes), index_root=args.output.parent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(canonical(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
