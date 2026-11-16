"""Replay reviewed real-source evidence without a live Virtuoso session.

PYTHONPATH=tools/ai/python python3 tools/ai/python/tests/replay_template_real.py \
  --evidence /private/native-test-directory --output /private/new-replay-directory
"""

import argparse
import json
from pathlib import Path

from template_real_labels import CASES, LABEL_SHA256, label_digest
from template_real_replay import replay
from template_real_review_support import check_inventory, check_record, sha256, write_json

from cadai.template_reuse_build import content_ref
from cadai.template_reuse_schema import validate_record
from cadai.template_source_qualification import require_qualified


def main():
    if label_digest() != LABEL_SHA256:
        raise ValueError("fixed review labels changed; a new review version is required")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for case in CASES:
        source = args.evidence / case["id"]
        record = json.loads((source / "record.json").read_text())
        inventory = json.loads((source / "oa-inventory.json").read_text())
        check_inventory(case, inventory)
        check_record(case, record)
        validate_record(record)
        require_qualified(record)
        if sha256(source / "capture.jsonl") != record["capture_sha256"]:
            raise ValueError("source capture evidence changed")
        if content_ref(record) != record["template_ref"]:
            raise ValueError("record content identity changed")
        records.append(record)
    result = dict(labels_digest=label_digest(), review="codex_review; human_expert_pending",
                  cases={})
    for case, record in zip(CASES, records):
        target = args.output / case["id"]
        target.mkdir()
        result["cases"][case["id"]] = replay(case, record, records, target)
    write_json(args.output / "replay.json", result)
    print(json.dumps(dict(cases=sum(len(v) for v in result["cases"].values()),
                          labels_digest=label_digest(), output=str(args.output))))


if __name__ == "__main__":
    main()
