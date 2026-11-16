"""Rebuild retained real-gate records and replay matching/requests without Virtuoso."""

import argparse
import json
from functools import partial
from pathlib import Path

from template_gate_labels import (
    CASES,
    LABEL_SHA256,
    LIBRARY,
    MASTERS,
    PARAMETERS,
    classification,
    label_digest,
)
from template_real_replay import labeled_target, replay, target_request
from template_real_review_support import check_inventory, check_record, reusable, sha256, write_json

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_reuse_build import content_ref
from cadai.template_source_qualification import require_qualified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert label_digest() == LABEL_SHA256
    target_topology = partial(labeled_target, masters=MASTERS)
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for c in CASES:
        directory = args.evidence / c["id"]
        check_inventory(c, json.loads((directory / "oa-inventory.json").read_text()),
                        library=LIBRARY)
        r = json.loads((directory / "record.json").read_text())
        assert sha256(directory / "capture.jsonl") == r["capture_sha256"]
        classes = {d.split()[0]: classification(d.split()[1]) for d in c["devices"]}
        assert reusable(make_template(read_capture(directory / "capture.jsonl"),
                                      classifications=classes)) == r
        check_record(c, r)
        require_qualified(r)
        assert content_ref(r) == r["template_ref"]
        records.append(r)
    bound = json.loads((args.evidence / "bindings.json").read_text())
    results = {}
    for c, r in zip(CASES, records):
        directory = args.output / c["id"]
        directory.mkdir()
        results[c["id"]] = replay(c, r, records, directory, target_factory=target_topology)
        request, _ = target_request(c, r, bound, target_factory=target_topology,
                                    parameters=PARAMETERS)
        if (args.evidence / c["id"] / "extract.json").exists():
            request["options"]["allowed_tiers"] = ["private"]
        assert request == json.loads((args.evidence / c["id"] / "request.json").read_text())
    write_json(args.output / "replay.json", dict(labels_digest=label_digest(), cases=results))
    print(json.dumps(dict(cases=sum(len(v) for v in results.values()), requests_equal=True,
                          labels_digest=label_digest())))


if __name__ == "__main__":
    main()
