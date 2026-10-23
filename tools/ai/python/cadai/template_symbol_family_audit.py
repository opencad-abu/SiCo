"""Audit automatic symbol-family selection against pinned catalog sources.

The audit is development-only and read-only.  It checks that every source in a
private build manifest selects exactly its intended family and that the family
produces a deterministic symbol plan.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .template_schema import TemplateError, TemplateUnavailable, canonical
from .template_symbol_family import family_plan, select_family
from .template_symbol_family_build import load_pinned_sources
from .template_symbol_family_registry import read_registry

AUDIT_FORMAT = "sico.symbol.family.audit.v1"


def _item(spec, record, status, selected=None, plan=None, message=None):
    source = record.get("source") or {}
    return {
        "library": source.get("lib"),
        "cell": source.get("cell"),
        "template_ref": spec["template_ref"],
        "expected_family": spec["family_id"],
        "selected_family": selected,
        "status": status,
        "preview_digest": (plan or {}).get("preview_digest"),
        "message": message,
    }


def audit_sources(catalog_path, manifest_path, registry_path):
    """Return an itemized automatic-selection report for one pinned manifest."""
    records, specs, manifest, catalog_path = load_pinned_sources(catalog_path, manifest_path)
    families, hints = read_registry(registry_path)
    items = []
    for spec in specs:
        record = records[(spec["library"], spec["cell"])]
        try:
            selected = select_family(record, families, hints)
        except TemplateError as exc:
            items.append(_item(spec, record, "ambiguous", message=str(exc)))
            continue
        if selected is None:
            items.append(_item(spec, record, "missing"))
            continue
        family_id, family = selected
        accepted = {family_id} | {alias.get("family_id") for alias in family.get("aliases", [])}
        if spec["family_id"] not in accepted:
            items.append(
                _item(
                    spec,
                    record,
                    "wrong_family",
                    family_id,
                    message="expected " + spec["family_id"],
                )
            )
            continue
        try:
            plan = family_plan(
                record,
                family,
                {
                    "library": "SicoFamilyAudit",
                    "cell": spec["cell"],
                    "port_map": {},
                    "style": "auto",
                    "label_policy": "drop",
                },
            )
        except (TemplateError, TemplateUnavailable) as exc:
            items.append(_item(spec, record, "plan_error", family_id, message=str(exc)))
            continue
        items.append(_item(spec, record, "selected", family_id, plan))
    counts = Counter(item["status"] for item in items)
    required = manifest["required_families"]
    missing_required = sorted(set(required) - set(families))
    covered = len(required) - len(missing_required)
    fraction = covered / len(required) if required else 1.0
    report = {
        "format": AUDIT_FORMAT,
        "catalog": str(catalog_path),
        "manifest": str(Path(manifest_path).resolve()),
        "registry": str(Path(registry_path).resolve()),
        "counts": {
            "targets": len(items),
            "selected": counts["selected"],
            "ambiguous": counts["ambiguous"],
            "missing": counts["missing"],
            "wrong_family": counts["wrong_family"],
            "plan_error": counts["plan_error"],
            "required_family_count": len(required),
            "required_family_covered": covered,
            "required_family_fraction": fraction,
            "coverage_target": 0.8,
            "coverage_target_met": fraction >= 0.8,
        },
        "missing_required_families": missing_required,
        "items": items,
    }
    report["passed"] = (
        report["counts"]["selected"] == len(items)
        and report["counts"]["coverage_target_met"]
        and not any(
            report["counts"][key] for key in ("ambiguous", "missing", "wrong_family", "plan_error")
        )
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = audit_sources(args.catalog, args.manifest, args.registry)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(canonical({key: report[key] for key in ("format", "counts", "passed")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
