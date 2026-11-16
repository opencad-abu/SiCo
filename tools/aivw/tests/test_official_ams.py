from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aivworkbench.design_ir import (
    OfficialAMSBindingError,
    build_config_binding_from_official_structure,
)


def _normalized() -> dict[str, object]:
    return {
        "schema_version": 1,
        "top": {"library": "lib", "cell": "top", "view": "schematic"},
        "defaults": {
            "liblist": ["lib", "basic"],
            "viewlist": ["config", "schematic"],
            "stoplist": ["spectre"],
        },
        "evidence": {
            "top_matches": True,
            "hed_traversal_ok": True,
            "catalog_authoritative": True,
            "catalog_top_opened": True,
            "explicit_instance_bindings_accounted": True,
            "success_marker": True,
            "fatal_log_marker": False,
            "unresolved_count": 0,
            "netlist_status": "success",
            "missing_artifacts": [],
        },
        "hed": {"provider": "Virtuoso/HED", "config_opened": True},
        "oa_catalog": {"provider": "dbAccess", "authoritative": True},
        "ams_unl": {
            "provider": "runams/AMS Unified Netlister",
            "bindings": [{"library": "lib", "cell": "top", "view": "schematic"}],
            "instance_bindings": [
                {
                    "parent_library": "lib",
                    "parent_cell": "top",
                    "parent_view": "schematic",
                    "instance": "U1",
                    "bound_library": "lib",
                    "bound_cell": "child",
                    "bound_view": "schematic",
                }
            ],
            "sources": {"binding": {"path": "binding.cfg"}},
        },
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, list[dict[str, object]], list[dict[str, object]]]:
    (tmp_path / "binding.cfg").write_text("official\n", encoding="utf-8")
    digest = hashlib.sha256((tmp_path / "binding.cfg").read_bytes()).hexdigest()
    rules = [
        {
            "name": "ams_rules",
            "sha256": digest,
            "artifact": {
                "path": "binding.cfg",
                "sha256": digest,
                "size": (tmp_path / "binding.cfg").stat().st_size,
                "media_type": "text/plain",
            },
        }
    ]
    disciplines = [{"net": "vout", "discipline": "electrical", "domain": "analog"}]
    return tmp_path, tmp_path, disciplines, rules


def test_official_structure_builds_target_bound_binding(tmp_path: Path) -> None:
    payload, source, disciplines, rules = _inputs(tmp_path)
    binding = build_config_binding_from_official_structure(
        _normalized(),
        target={"library": "lib", "cell": "top", "config_view": "config"},
        source_generation="a" * 64,
        payload_root=payload,
        source_root=source,
        evidence=_normalized()["evidence"],
        discipline_bindings=disciplines,
        connect_rules=rules,
    )
    assert binding.identity["view"] == "config"
    assert binding.payload["instance_bindings"][0]["instance"] == "U1"
    assert binding.payload["source_artifacts"][0]["sha256"] == rules[0]["sha256"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("unresolved_count", 1),
        ("success_marker", False),
        ("fatal_log_marker", True),
        ("netlist_status", "failed"),
    ],
)
def test_official_structure_blocks_incomplete_evidence(
    tmp_path: Path, field: str, value: object
) -> None:
    payload, source, disciplines, rules = _inputs(tmp_path)
    normalized = _normalized()
    normalized["evidence"][field] = value  # type: ignore[index]
    with pytest.raises(OfficialAMSBindingError, match="official AMS"):
        build_config_binding_from_official_structure(
            normalized,
            target={"library": "lib", "cell": "top", "config_view": "config"},
            source_generation="a" * 64,
            payload_root=payload,
            source_root=source,
            evidence=normalized["evidence"],
            discipline_bindings=disciplines,
            connect_rules=rules,
        )


def test_official_structure_rejects_source_outside_payload(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    source = tmp_path / "source"
    payload.mkdir()
    source.mkdir()
    (source / "binding.cfg").write_text("official\n", encoding="utf-8")
    normalized = _normalized()
    digest = hashlib.sha256((source / "binding.cfg").read_bytes()).hexdigest()
    rules = [{
        "name": "ams_rules",
        "sha256": digest,
        "artifact": {"path": "binding.cfg", "sha256": digest, "size": 9, "media_type": "text/plain"},
    }]
    with pytest.raises(OfficialAMSBindingError, match="outside payload"):
        build_config_binding_from_official_structure(
            normalized,
            target={"library": "lib", "cell": "top", "config_view": "config"},
            source_generation="a" * 64,
            payload_root=payload,
            source_root=source,
            discipline_bindings=[{"net": "vout", "discipline": "electrical", "domain": "analog"}],
            connect_rules=rules,
        )


def test_official_structure_requires_connect_rule_media_type(tmp_path: Path) -> None:
    payload, source, disciplines, rules = _inputs(tmp_path)
    rules[0]["artifact"] = dict(rules[0]["artifact"])
    rules[0]["artifact"].pop("media_type")  # type: ignore[index]
    with pytest.raises(OfficialAMSBindingError, match="media_type"):
        build_config_binding_from_official_structure(
            _normalized(),
            target={"library": "lib", "cell": "top", "config_view": "config"},
            source_generation="a" * 64,
            payload_root=payload,
            source_root=source,
            evidence=_normalized()["evidence"],
            discipline_bindings=disciplines,
            connect_rules=rules,
        )
