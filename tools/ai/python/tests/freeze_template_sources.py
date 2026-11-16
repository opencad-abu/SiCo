"""Freeze read-only OA source candidates; file presence never certifies reuse or independence."""

import argparse
import hashlib
import json
from pathlib import Path

from cadai.template_schema import digest

# Prior source selection is documented in SCHEMATIC_TEMPLATE_FLOW.md.
# Reserve related variants together; these are candidates pending ancestry audit.
SELECTION = (
    ("saradc", "clockdriver", "saradc_clockdriver"),
    ("saradcII", "clockdriver", "saradc_clockdriver"),
    ("saradc", "backplate_clamp", "saradc_backplate_clamp"),
    ("saradcII", "backplate_clamp", "saradc_backplate_clamp"),
    ("saradc", "latch_buffer", "saradc_latch_buffer"),
    ("saradcII", "latch_buffer", "saradc_latch_buffer"),
    ("saradcII", "comparator_ref", "saradc_comparator"),
)


def freeze(root):
    root = root.resolve()
    files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in [root / "cds.lib", root / "SARADC/cds.lib",
                       root / "FRACNPLL/cds.lib", root / "LDO_IP/cds.lib"]}
    candidates = []
    for library, cell, family in SELECTION:
        path = root / "SARADC/oa" / library / cell / "schematic/sch.oa"
        present = path.is_file()
        candidates.append(dict(
            library=library, cell=cell, view="schematic", family=family,
            relative_path=str(path.relative_to(root)),
            oa_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if present else None,
            status="candidate_pending_review" if present else "unavailable",
            independence="pending_family_and_content_audit", qualification="not_run",
            expected_status=None, human_review="pending",
        ))
    return dict(
        schema="cad.template.source-candidates.v1", root=str(root), files=files,
        inventory_digest=digest(dict(files=files, candidates=candidates)),
        candidates=candidates,
        prior_use_evidence="tools/ai/docs/SCHEMATIC_TEMPLATE_FLOW.md:91-108",
        exclusions=[
            "amsLDO: development and routing regression",
            "zambezi45: AoT catalog and prior extraction",
            "saradc/comparator,latchonly,analog_mux_1: prior extraction",
            "saradcII/boosted_SH_switch,mos_switch: prior extraction",
        ],
        human_expert_pending=True, held_out_certified=False,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = freeze(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
    print(json.dumps(dict(output=str(args.output), candidates=len(value["candidates"]),
                          inventory_digest=value["inventory_digest"],
                          held_out_certified=False, human_expert_pending=True)))
