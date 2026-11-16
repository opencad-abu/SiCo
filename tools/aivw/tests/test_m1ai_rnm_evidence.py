from __future__ import annotations

import json
from pathlib import Path

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.m1ai_rnm_evidence import parse_rnm_evidence_log, run_m1_ai_rnm_evidence
from aivworkbench.profiles import Profile
from aivworkbench.workspace import resolve_launch_paths, sha256_file


def _profile(tmp_path: Path) -> Profile:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{}", encoding="utf-8")
    return Profile(
        name="test",
        display_name="Test",
        workspace_root=tmp_path,
        setup_script=tmp_path / "setup.bashrc",
        modules=(),
        tools=(),
        inputs=(),
        projects={},
        pilots={},
        source_path=profile_path,
    )


def _source_manifest(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    model = root / "ai-generation" / "comparator_new.rnm.sv"
    model.parent.mkdir(parents=True)
    model.write_text("module comparator_new; endmodule\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "rnm-source",
                "kind": "m1-ai-rnm-generation-check-tb",
                "status": "PASS",
                "artifacts": [
                    {
                        "path": "ai-generation/comparator_new.rnm.sv",
                        "sha256": sha256_file(model),
                        "size": model.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_parse_rnm_evidence_log_requires_three_unique_cases() -> None:
    text = "\n".join(
        (
            "AIVW_RNM_EVIDENCE case=nominal-positive decision=positive out=1.2 outb=0",
            "AIVW_RNM_EVIDENCE case=nominal-negative decision=negative out=0 outb=1.2",
            "AIVW_RNM_EVIDENCE case=nominal-zero decision=tie out=0.6 outb=0.6",
        )
    )
    cases = parse_rnm_evidence_log(text)
    assert cases["nominal-positive"]["type"] == "positive_differential"
    assert cases["nominal-zero"]["measurements"]["out"] == pytest.approx(0.6)


def test_parse_rnm_evidence_log_rejects_duplicate_or_non_finite_values() -> None:
    duplicate = "AIVW_RNM_EVIDENCE case=nominal-positive decision=positive out=1 outb=0\n" * 2
    with pytest.raises(EnvironmentError, match="duplicate"):
        parse_rnm_evidence_log(duplicate)
    non_finite = "\n".join(
        (
            "AIVW_RNM_EVIDENCE case=nominal-positive decision=positive out=nan outb=0",
            "AIVW_RNM_EVIDENCE case=nominal-negative decision=negative out=0 outb=1.2",
            "AIVW_RNM_EVIDENCE case=nominal-zero decision=tie out=0.6 outb=0.6",
        )
    )
    with pytest.raises(EnvironmentError, match="non-finite"):
        parse_rnm_evidence_log(non_finite)


def test_rnm_evidence_runner_requires_source_manifest(tmp_path: Path) -> None:
    with pytest.raises(EnvironmentError, match="source manifest"):
        run_m1_ai_rnm_evidence(
            _profile(tmp_path),
            resolve_launch_paths(tmp_path),
            source_manifest=tmp_path / "missing.json",
            xrun_path="/missing/xrun",
        )
