from __future__ import annotations

import json
from pathlib import Path

from dmyfillpy.profile import load_profile
from dmyfillpy.runner import run_fill


def test_run_fill_parses_rdb_and_writes_plan(tmp_path: Path) -> None:
    deck = tmp_path / "fill.cal"
    deck.write_text(
        "LAYOUT PATH \"old.gds\"\n"
        "LAYOUT PRIMARY \"OLD\"\n"
        "DRC RESULTS DATABASE \"old.gds\" GDSII\n"
        "DRC SUMMARY REPORT \"old.sum\"\n"
        "DRC MAXIMUM RESULTS ALL\n"
        "#DEFINE FILL_DM1\n"
        "DRC CHECK MAP DM1_FILL 31 1 AUTOREF\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "fill.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema": "sico.dmyfill.profile.v1",
                "deck": deck.name,
                "layers": {"M1": {"fill_define": "FILL_DM1"}},
                "rules": {"DM1_FILL": {"layer": "M1", "purpose": "dummy"}},
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    fake = tmp_path / "calibre"
    fake.write_text(
        "#!/bin/sh\n"
        "cat > " + str(run_dir / "fill.rdb") + " <<'EOF'\n"
        "TOP 1000\n"
        "DM1_FILL\n"
        "1 1 1 now\n"
        "p 1 4\n"
        "0 0\n"
        "1000 0\n"
        "1000 1000\n"
        "0 1000\n"
        "EOF\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = run_fill(
        load_profile(profile_path),
        layout_path=tmp_path / "input.gds",
        top_cell="TOP",
        layers=("M1",),
        run_dir=run_dir,
        calibre=str(fake),
    )

    assert result.shape_count == 1
    assert result.rule_count == 1
    assert "RECT M1 dummy 0 0 1 1" in result.plan.read_text()
    manifest = json.loads(result.manifest.read_text())
    assert manifest["shape_count"] == 1
    assert manifest["top_cell"] == "TOP"


def test_run_fill_expands_cell_region_by_profile_margin(tmp_path: Path) -> None:
    deck = tmp_path / "fill.cal"
    deck.write_text(
        "LAYOUT PATH \"old.gds\"\n"
        "LAYOUT PRIMARY \"OLD\"\n"
        "DRC RESULTS DATABASE \"old.gds\" GDSII\n"
        "DRC SUMMARY REPORT \"old.sum\"\n"
        "DRC MAXIMUM RESULTS ALL\n"
        "VARIABLE xLB 0\n"
        "VARIABLE yLB 0\n"
        "VARIABLE xRT 1\n"
        "VARIABLE yRT 1\n"
        "#DEFINE FILL_DM1\n"
        "DRC CHECK MAP DM1_FILL 31 1 AUTOREF\n",
        encoding="utf-8",
    )
    profile_path = tmp_path / "fill.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema": "sico.dmyfill.profile.v1",
                "deck": deck.name,
                "layers": {"M1": {"fill_define": "FILL_DM1"}},
                "rules": {"DM1_FILL": {"layer": "M1", "purpose": "dummy"}},
                "options": {"region_margin": 2.5},
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    fake = tmp_path / "calibre"
    fake.write_text(
        "#!/bin/sh\n"
        "cat > " + str(run_dir / "fill.rdb") + " <<'EOF'\n"
        "TOP 1000\n"
        "DM1_FILL\n"
        "1 1 1 now\n"
        "p 1 4\n"
        "0 0\n"
        "1000 0\n"
        "1000 1000\n"
        "0 1000\n"
        "EOF\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)

    result = run_fill(
        load_profile(profile_path),
        layout_path=tmp_path / "input.gds",
        top_cell="TOP",
        layers=("M1",),
        run_dir=run_dir,
        region=(1.0, 1.0, 2.0, 2.0),
        expand_region=True,
        calibre=str(fake),
    )

    generated = result.deck.read_text(encoding="utf-8")
    assert "VARIABLE xLB -1.5" in generated
    assert "VARIABLE xRT 4.5" in generated
    manifest = json.loads(result.manifest.read_text())
    assert manifest["requested_region"] == [1.0, 1.0, 2.0, 2.0]
    assert manifest["region"] == [-1.5, -1.5, 4.5, 4.5]
