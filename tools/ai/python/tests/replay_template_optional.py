"""Offline replay of private real-source omission evidence; no bundled source assets."""

import argparse
import json

from template_optional_labels import INVERTER, LABEL_SHA256, OPAMP, label_digest
from template_optional_replay import omission_request, replay
from template_optional_source import check_inventory, check_record, optional_record
from template_real_labels import classification
from template_real_review_support import reusable, sha256, write_json

from cadai.template_build import make_template
from cadai.template_capture import read_capture
from cadai.template_reuse_build import content_ref
from cadai.template_schema import TemplateError
from cadai.template_source_qualification import require_qualified


def main():
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert label_digest() == LABEL_SHA256
    args.output.mkdir(parents=True, exist_ok=False)
    bases = {}
    for case in (OPAMP, INVERTER):
        directory = args.evidence / case["id"]
        check_inventory(case, json.loads((directory / "oa-inventory.json").read_text()))
        saved = json.loads((directory / "base-record.json").read_text())
        assert saved["capture_sha256"] == sha256(directory / "capture.jsonl")
        classes = {row.split()[0]: classification(row.split()[1]) for row in case["devices"]}
        base = make_template(read_capture(directory / "capture.jsonl"), classifications=classes)
        assert base == saved
        check_record(case, base)
        bases[case["id"]] = base
    try:
        reusable(bases["inverter"])
    except TemplateError as exc:
        assert INVERTER["rejection"] in str(exc)
    else:
        raise AssertionError("inherited source unexpectedly qualified")
    record = json.loads((args.evidence / "opamp/record.json").read_text())
    assert record == optional_record(OPAMP, bases["opamp"])
    assert record["template_ref"] == content_ref(record)
    require_qualified(record)
    cases = replay(record)
    bound = json.loads((args.evidence / "bindings.json").read_text())
    request, audit = omission_request(record, bound)
    write_json(args.output / "request.json", request)
    write_json(args.output / "replay.json", dict(labels_digest=label_digest(), cases=cases,
                                                adaptation=audit,
                                                inverter_rejection=INVERTER["rejection"]))
    print(json.dumps(dict(matching_cases=len(cases), adaptation_rejections=len(audit["rejected"]),
                          inherited_rejections=1, labels_digest=label_digest())))


if __name__ == "__main__":
    main()
