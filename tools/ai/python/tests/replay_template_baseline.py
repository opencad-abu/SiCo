"""Replay the P0 synthetic corpus into private evidence, outside runtime packaging.

Run from tools/ai/python: python tests/replay_template_baseline.py --output /path/to/.sico/evidence.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from template_search_corpus import VERSION, cases  # noqa: E402

from cadai.template_schema import RULE_VERSION, canonical, digest  # noqa: E402
from cadai.template_search import MATCHER_VERSION, search_exact  # noqa: E402


def percentile(samples, fraction):
    return sorted(samples)[max(0, math.ceil(len(samples) * fraction) - 1)]


def source_identity():
    root = Path(__file__).resolve().parents[3]
    python_root = root / "tools/ai/python"
    # Include uncommitted and untracked implementation, not just HEAD.
    sources = [
        *sorted((python_root / "cadai").glob("template_*.py")),
        python_root / "cadai/circuit_spec_schema.py",
        Path(__file__).resolve(),
        Path(__file__).with_name("template_search_corpus.py"),
    ]
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return {"base_commit": commit, "source_digest": digest(hashes), "files": hashes}


def replay(split="all"):
    started = time.monotonic()
    corpus = cases()
    selected = [case for case in corpus if split == "all" or case["split"] == split]
    acquisition_ms = (time.monotonic() - started) * 1000
    before = resource.getrusage(resource.RUSAGE_SELF)
    reports, timings = [], []
    for case in selected:
        result = search_exact(
            case["target"],
            case["candidates"],
            {"max_query_time_ms": 10000, "max_match_states": 200000},
            port_maps=case["port_maps"],
        )
        passed = result["status"] == case["expected_status"]
        if case["expected_mapping"] is not None:
            passed &= (
                result["best_match"] is not None
                and result["best_match"]["mapping"] == case["expected_mapping"]
            )
        timings.append(result["diagnostics"]["elapsed_ms"])
        reports.append(
            {
                "case_id": case["id"],
                "split": case["split"],
                "passed": passed,
                "input_digest": digest(case),
                "expected_status": case["expected_status"],
                "expected_mapping": case["expected_mapping"],
                "actual": result,
            }
        )
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "schema": "cad.template.baseline-report.v1",
        "dataset": {
            "version": VERSION,
            "sha256": digest(corpus),
            "split": split,
            "cases": len(reports),
            "label_source": "synthetic_structural_invariant",
            "expert_reviewed": False,
        },
        "source": source_identity(),
        "versions": {
            "python": platform.python_version(),
            "matcher": MATCHER_VERSION,
            "normalizer": RULE_VERSION,
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpus": os.cpu_count(),
            "input_storage": "in_memory_generated",
            "cache": "disabled",
        },
        "summary": {
            "passed": sum(r["passed"] for r in reports),
            "failed": sum(not r["passed"] for r in reports),
            "statuses": dict(Counter(r["actual"]["status"] for r in reports)),
            "inconclusive": sum(r["actual"]["status"] == "inconclusive" for r in reports),
            "plan_count": sum(r["actual"]["plan"] is not None for r in reports),
        },
        "measurements": {
            "acquisition_ms": acquisition_ms,
            "search_ms": {
                "p50": percentile(timings, 0.50),
                "p95": percentile(timings, 0.95),
                "p99": percentile(timings, 0.99),
                "first": timings[0],
            },
            "max_rss_kib": usage.ru_maxrss,
            "block_input_operations": usage.ru_inblock - before.ru_inblock,
            "block_output_operations": usage.ru_oublock - before.ru_oublock,
            "max_response_bytes": max(len(canonical(r["actual"]).encode()) for r in reports),
        },
        "cases": reports,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("all", "development", "acceptance"), default="all")
    args = parser.parse_args(argv)
    report = replay(args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Evidence is private and immutable; a new run chooses a new filename.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        canonical(
            {
                "output": str(args.output),
                "dataset": report["dataset"],
                "summary": report["summary"],
                "measurements": report["measurements"],
            }
        )
    )
    return int(bool(report["summary"]["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
