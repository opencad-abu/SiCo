"""Audit private symbol-family provenance and distribution boundaries."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .template_schema import TemplateError, canonical
from .template_symbol_family import FAMILY_FORMAT
from .template_symbol_family_registry import INDEX_FORMAT, read_registry

REPORT_FORMAT = "sico.symbol.family.provenance.v1"
LICENSE_FORMAT = "sico.symbol.family.license.v1"


def load_licenses(path):
    """Load an explicit private license review manifest, if supplied."""
    if not path:
        return {}
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TemplateError("cannot read family license manifest: " + str(exc)) from exc
    if data.get("format") != LICENSE_FORMAT or not isinstance(data.get("families"), dict):
        raise TemplateError("unsupported family license manifest")
    result = {}
    for family_id, row in data["families"].items():
        if not isinstance(family_id, str) or not isinstance(row, dict):
            raise TemplateError("invalid family license entry")
        license_text = row.get("license")
        distributable = row.get("distributable")
        internal_distribution = row.get("internal_distribution", distributable)
        if not isinstance(license_text, str) or not license_text.strip():
            raise TemplateError("family license entry requires license text: " + family_id)
        if type(distributable) is not bool:
            raise TemplateError("family license entry requires distributable boolean: " + family_id)
        if type(internal_distribution) is not bool:
            raise TemplateError(
                "family license entry requires internal_distribution boolean: " + family_id
            )
        if distributable and not internal_distribution:
            raise TemplateError(
                "external distribution requires internal distribution: " + family_id
            )
        result[family_id] = {
            "license": license_text.strip(),
            "distributable": distributable,
            "internal_distribution": internal_distribution,
            "notice": str(row.get("notice", "")).strip(),
            "source": str(row.get("source", "")).strip(),
        }
    return result


def _index(root):
    path = Path(root) / "index.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TemplateError("cannot read family index: " + str(exc)) from exc
    if data.get("format") != INDEX_FORMAT:
        raise TemplateError("unsupported family index format")
    return data


def audit_registry(registry, license_manifest=None):
    """Return a non-geometric provenance inventory for one private registry."""
    registry = Path(registry)
    families, hints = read_registry(registry)
    index = _index(registry)
    licenses = load_licenses(license_manifest)
    unknown = sorted(set(licenses) - set(families))
    if unknown:
        raise TemplateError("license manifest references unknown family: " + ", ".join(unknown))
    rows, errors = [], []
    for family_id in sorted(families):
        family = families[family_id]
        aliases = family.get("aliases", [])
        if not aliases:
            errors.append(f"{family_id}: family has no source alias")
        sources = []
        for alias in aliases:
            source = alias.get("source") or {}
            source_ref = source.get("template_ref")
            if not isinstance(source_ref, str) or not source_ref:
                errors.append(f"{family_id}: alias has no template_ref")
            sources.append(
                {
                    "family_id": alias.get("family_id"),
                    "library": source.get("lib"),
                    "cell": source.get("cell"),
                    "template_ref": source_ref,
                }
            )
        review = licenses.get(family_id)
        if review:
            if review["distributable"]:
                license_status = "reviewed_distributable"
            elif review["internal_distribution"]:
                license_status = "reviewed_internal_only"
            else:
                license_status = "reviewed_private"
            distributable = review["distributable"]
            internal_distribution = review["internal_distribution"]
        else:
            license_status = "unknown"
            distributable = False
            internal_distribution = False
        rows.append(
            {
                "family_id": family_id,
                "family_version": family.get("version"),
                "signature": family.get("signature"),
                "slot_count": len(family.get("slots", [])),
                "annotation_count": len(family.get("annotations", [])),
                "dynamic_label_count": len(family.get("dropped_labels", [])),
                "cell_hints": hints.get(family_id, []),
                "sources": sources,
                "license_status": license_status,
                "distributable": distributable,
                "internal_distribution": internal_distribution,
                "license_review": review,
            }
        )
    unknown_license = sum(row["license_status"] == "unknown" for row in rows)
    report = {
        "format": REPORT_FORMAT,
        "registry": str(registry.resolve()),
        "index_format": INDEX_FORMAT,
        "index_family_count": len(index.get("families", {})),
        "family_format": FAMILY_FORMAT,
        "families": rows,
        "counts": {
            "families": len(rows),
            "sources": sum(len(row["sources"]) for row in rows),
            "unknown_license": unknown_license,
            "distributable": sum(row["distributable"] for row in rows),
            "internal_distribution": sum(row["internal_distribution"] for row in rows),
            "errors": len(errors),
        },
        "boundary": {
            "default": "private_only_until_source_license_review",
            "geometry_in_report": False,
            "internal_distribution_ready": unknown_license == 0
            and all(row["internal_distribution"] for row in rows),
            "distribution_ready": unknown_license == 0
            and all(row["distributable"] for row in rows),
        },
        "errors": errors,
        "integrity_passed": not errors,
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--license-manifest", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-distributable", action="store_true")
    parser.add_argument("--require-internal-distribution", action="store_true")
    args = parser.parse_args(argv)
    report = audit_registry(args.registry, args.license_manifest)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(canonical(report) + "\n")
    print(
        canonical(
            {key: report[key] for key in ("format", "counts", "boundary", "integrity_passed")}
        )
    )
    if not report["integrity_passed"]:
        return 1
    if args.require_distributable and not report["boundary"]["distribution_ready"]:
        return 1
    if args.require_internal_distribution and not report["boundary"]["internal_distribution_ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
