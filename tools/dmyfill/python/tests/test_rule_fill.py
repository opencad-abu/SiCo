from __future__ import annotations

from pathlib import Path

from dmyfillpy.rule_fill import generate_rule_deck
from dmyfillpy.rules import load_rule_spec
from dmyfillpy.runner import run_rule_fill


def _spec(tmp_path: Path):
    source = tmp_path / "dmyfill.toml"
    source.write_text(
        """\
format = "sico.dmyfill.rules.v1"
unit = "um"
source = "test rules"
target_density = 0.40
edge = 0.0

[defaults]
wide_width = 4.5
wide_space = 1.5
blockers = { FW = 5.0, LOGO = 0.0 }

[groups.m1]
layers = ["M1", "M2"]
width = [0.3, 3.0]
space = 0.3
metal_space = 0.3
area = [0.24, 80]
tile = [0.5, 2.0]
""",
        encoding="utf-8",
    )
    layer_map = tmp_path / "tech.layermap"
    layer_map.write_text(
        "M1 drawing 31 0\n"
        "M2 drawing 32 0\n"
        "FW drawing 95 0\n"
        "LOGO drawing 158 0\n"
        "DMEXCL dummy1 150 1\n"
        "DMEXCL dummy2 150 2\n",
        encoding="utf-8",
    )
    return load_rule_spec(source, layer_map)


def test_generated_deck_uses_binary_or_and_extents(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    generated = generate_rule_deck(
        spec,
        layout_path=tmp_path / "input.gds",
        top_cell="TOP",
        output_rdb=tmp_path / "fill.rdb",
        summary_path=tmp_path / "fill.sum",
        layers=("M1", "M2"),
        region=(1.0, 2.0, 9.0, 10.0),
    )
    text = generated.text

    assert generated.rule_names == ("M1_FILL", "M2_FILL")
    assert "LAYER IN_M1 31 0" in text
    assert "LAYER BLK_FW 95 0" in text
    assert "LAYER BLK_DMEXCL_1 150 1" in text
    window = next(
        line for line in text.splitlines() if line.startswith("LAYER ChipWindow")
    )
    assert len(window.split()) == 3
    assert "M1_BLK_OR_1 = M1_METAL_BLOCK OR M1_WIDE_BLOCK" in text
    assert "M1_BLK = M1_BLK_OR_3 OR BLK_DMEXCL_1" in text
    assert "M1_FILL { EXTENTS GEOM_M1 }" in text
    assert "RECTANGLES" in text
    assert "DRC CHECK MAP" not in text
    assert all(line.count(" OR ") <= 1 for line in text.splitlines())


def test_rule_fill_runs_fake_calibre_and_writes_rect_plan(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    run_dir = tmp_path / "run"
    fake = tmp_path / "calibre"
    fake.write_text(
        "#!/bin/sh\n"
        f"cat > {run_dir}/fill.rdb <<'EOF'\n"
        "TOP 1000\n"
        "M1_FILL\n"
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

    result = run_rule_fill(
        spec,
        layout_path=tmp_path / "input.gds",
        top_cell="TOP",
        layers=("M1",),
        run_dir=run_dir,
        region=(0.0, 0.0, 10.0, 10.0),
        calibre=str(fake),
    )

    assert result.shape_count == 1
    assert "RECT M1 dummy 0 0 1 1" in result.plan.read_text()
    assert '"source": "rule"' in result.manifest.read_text()
    assert "M1_BLK_OR_" in result.deck.read_text()
