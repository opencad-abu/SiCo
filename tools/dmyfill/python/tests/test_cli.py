from __future__ import annotations

import json
from pathlib import Path

from dmyfillpy.cli import main


def _profile(tmp_path: Path) -> Path:
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
    profile = {
        "schema": "sico.dmyfill.profile.v1",
        "deck": deck.name,
        "layers": {"M1": {"fill_define": "FILL_DM1"}},
        "rules": {"DM1_FILL": {"layer": "M1", "purpose": "dummy"}},
    }
    path = tmp_path / "fill.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path


def test_cli_deck_and_plan(tmp_path: Path, capsys) -> None:
    profile = _profile(tmp_path)
    generated = tmp_path / "run.cal"
    assert main([
        "deck",
        "--profile", str(profile),
        "--output", str(generated),
        "--layout", str(tmp_path / "in.gds"),
        "--top-cell", "TOP",
        "--results", str(tmp_path / "fill.rdb"),
        "--summary", str(tmp_path / "fill.sum"),
        "--layers", "M1",
    ]) == 0
    assert "ASCII" in generated.read_text()
    assert "DRC CHECK MAP" not in generated.read_text()

    rdb = tmp_path / "fill.rdb"
    rdb.write_text(
        "TOP 1000\nDM1_FILL\n1 6 1 now\np 1 4\n0 0\n1 0\n1 1\n0 1\n",
        encoding="utf-8",
    )
    plan = tmp_path / "fill.plan"
    manifest = tmp_path / "fill.json.out"
    assert main([
        "plan",
        "--profile", str(profile),
        "--rdb", str(rdb),
        "--output", str(plan),
        "--run-id", "run1",
        "--manifest", str(manifest),
    ]) == 0
    assert plan.read_text().startswith("# dmyfill-plan v1")
    data = json.loads(manifest.read_text())
    assert data["shape_count"] == 1
    assert data["top_cell"] == "TOP"


def test_cli_reports_invalid_profile(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["plan", "--profile", str(bad), "--rdb", "x", "--output", "y", "--run-id", "z"]) == 1
    assert "schema" in capsys.readouterr().err


def test_cli_resolves_toml_as_rule_source(tmp_path: Path) -> None:
    source = tmp_path / "dmyfill.toml"
    source.write_text(
        """\
format = "sico.dmyfill.rules.v1"
unit = "um"
source = "test"
target_density = 0.4
edge = 0.0

[groups.m1]
layers = ["M1"]
width = [0.3, 3.0]
space = 0.3
area = [0.24, 80]
tile = [0.5, 2.0]
""",
        encoding="utf-8",
    )
    layer_map = tmp_path / "tech.layermap"
    layer_map.write_text("M1 drawing 31 0\n", encoding="utf-8")
    output = tmp_path / "resolve.txt"

    assert main([
        "resolve",
        "--toml", str(source),
        "--layer-map", str(layer_map),
        "--output", str(output),
    ]) == 0
    text = output.read_text()
    assert "mode=rule" in text
    assert "fill_source=rule" in text
    assert "layer=M1" in text


def test_cli_run_reports_toml_priority_warning(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    deck = tmp_path / "foundry.cal"
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
    layer_map = tmp_path / "tech.layermap"
    layer_map.write_text("M1 drawing 31 0\n", encoding="utf-8")
    rule = tmp_path / "dmyfill.toml"
    rule.write_text(
        """\
format = "sico.dmyfill.rules.v1"
unit = "um"
source = "test"
target_density = 0.4
edge = 0.0

[groups.m1]
layers = ["M1"]
width = [0.3, 3.0]
space = 0.3
area = [0.24, 80]
tile = [0.5, 2.0]
""",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    fake = tmp_path / "calibre"
    fake.write_text(
        "#!/bin/sh\n"
        f"cat > {run_dir}/fill.rdb <<'EOF'\n"
        "TOP 1000\nM1_FILL\n1 1 1 now\np 1 4\n"
        "0 0\n1000 0\n1000 1000\n0 1000\nEOF\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("DMYFILL_FILE", f"default,{deck}")
    monkeypatch.setenv("DMYFILL_TOML", str(rule))
    monkeypatch.setenv("TECH_LAYER_MAP", str(layer_map))
    monkeypatch.setenv("SICO_DMYFILL_CACHE_DIR", str(tmp_path / "cache"))

    assert main([
        "run",
        "--layout", str(tmp_path / "input.gds"),
        "--top-cell", "TOP",
        "--layers", "M1",
        "--run-dir", str(run_dir),
        "--calibre", str(fake),
    ]) == 0
    assert "DMYFILL_TOML ignored: DMYFILL_FILE has priority" in capsys.readouterr().out
