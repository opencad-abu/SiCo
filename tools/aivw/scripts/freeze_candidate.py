#!/usr/bin/env python3
"""Freeze one admitted candidate (public evidence only; no EDA, no private data)."""

import argparse
import json
from pathlib import Path

from aivworkbench.qualification.candidate_freeze import freeze_candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--crosscheck", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--single-event", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--admission", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    args = parser.parse_args()
    summary = freeze_candidate(args.matrix, args.reference, args.crosscheck, args.source,
        args.single_event, args.sequence, args.admission, args.policy, args.experiment)
    print(json.dumps({"run_id": summary["run_id"], "integrity": summary["integrity"],
                      "candidate_sha256": summary["record"]["candidate_sha256"]}))


if __name__ == "__main__":
    main()
