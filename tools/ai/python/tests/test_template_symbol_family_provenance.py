"""Private family provenance must remain explicit and non-distributable by default."""

import json

from test_draw_symbol import gate_record

from cadai.template_symbol_family import build_family, dedupe_families
from cadai.template_symbol_family_provenance import audit_registry
from cadai.template_symbol_family_registry import write_registry


def _registry(tmp_path):
    family = build_family(gate_record("INVX1", labels=True), "inv")
    root = tmp_path / "families"
    write_registry(root, dedupe_families([family]), {"inv": ["inv"]})
    return root


def test_provenance_defaults_to_private_unknown_license(tmp_path):
    report = audit_registry(_registry(tmp_path))
    assert report["integrity_passed"]
    assert report["counts"]["families"] == 1
    assert report["counts"]["unknown_license"] == 1
    assert report["boundary"]["distribution_ready"] is False
    assert report["boundary"]["internal_distribution_ready"] is False
    assert report["families"][0]["license_status"] == "unknown"
    assert report["families"][0]["sources"][0]["template_ref"]
    assert "body" not in report["families"][0]


def test_provenance_requires_an_explicit_license_review(tmp_path):
    registry = _registry(tmp_path)
    manifest = tmp_path / "license.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.license.v1",
                "families": {
                    "inv": {
                        "license": "site-private-review",
                        "distributable": False,
                        "notice": "not for redistribution",
                    }
                },
            }
        )
    )
    report = audit_registry(registry, manifest)
    assert report["families"][0]["license_status"] == "reviewed_private"
    assert report["boundary"]["distribution_ready"] is False
    manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.license.v1",
                "families": {
                    "inv": {
                        "license": "site-internal-review",
                        "internal_distribution": True,
                        "distributable": False,
                        "notice": "internal use and distribution only",
                    }
                },
            }
        )
    )
    report = audit_registry(registry, manifest)
    assert report["families"][0]["license_status"] == "reviewed_internal_only"
    assert report["boundary"]["internal_distribution_ready"] is True
    assert report["boundary"]["distribution_ready"] is False
    manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.family.license.v1",
                "families": {"inv": {"license": "fixture-license", "distributable": True}},
            }
        )
    )
    report = audit_registry(registry, manifest)
    assert report["families"][0]["license_status"] == "reviewed_distributable"
    assert report["boundary"]["internal_distribution_ready"] is True
    assert report["boundary"]["distribution_ready"] is True
