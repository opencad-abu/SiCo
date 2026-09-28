from __future__ import annotations

from pathlib import Path

import pytest

from rcepy.dspf import DspfRepository, build_index, resistance_highlight_regions


def _repository(tmp_path: Path, body: str) -> DspfRepository:
    source = tmp_path / "highlight.dspf"
    source.write_text(body, encoding="ascii")
    result = build_index(source, cache_dir=tmp_path / "cache")
    return DspfRepository(result.index_path)


def test_resistance_highlight_regions_cover_located_r_edges(tmp_path: Path) -> None:
    with _repository(
        tmp_path,
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\n"
        "*|S (A:1 1 2)\n*|S (A:2 5 4)\n"
        "R1 A:1 A:2 1 $a=1 $w=0.2\n.ENDS\n",
    ) as repository:
        regions = resistance_highlight_regions(repository, "A")

    assert len(regions) == 1
    left, bottom, right, top = regions[0]
    assert left < 1 and bottom < 2 and right > 5 and top > 4


def test_resistance_highlight_regions_use_single_endpoint_and_skip_unlocated(
    tmp_path: Path,
) -> None:
    with _repository(
        tmp_path,
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\n"
        "*|S (A:1 3 7)\nR1 A:1 A:2 1\nR2 A:2 A:3 2\n.ENDS\n",
    ) as repository:
        regions = resistance_highlight_regions(repository, "A")

    assert len(regions) == 1
    assert regions[0][0] < 3 < regions[0][2]
    assert regions[0][1] < 7 < regions[0][3]


def test_resistance_highlight_regions_are_bounded_and_deterministic(
    tmp_path: Path,
) -> None:
    lines = ["*|DSPF 1.0", ".SUBCKT top A", "*|NET A 0"]
    for index in range(12):
        lines.extend((
            f"*|S (A:{index} {index} {index % 3})",
            f"R{index} A:{index} A:{index + 1} 1",
        ))
    lines.extend(("*|S (A:12 12 0)", ".ENDS"))
    with _repository(tmp_path, "\n".join(lines) + "\n") as repository:
        first = resistance_highlight_regions(repository, "A", max_regions=3)
        second = resistance_highlight_regions(repository, "A", max_regions=3)

    assert first == second
    assert len(first) == 3


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "2"])
def test_resistance_highlight_limits_require_positive_integers(
    tmp_path: Path, value: object,
) -> None:
    with _repository(
        tmp_path,
        "*|DSPF 1.0\n.SUBCKT top A\n*|NET A 0\n.ENDS\n",
    ) as repository:
        with pytest.raises(ValueError, match="max_regions must be a positive integer"):
            resistance_highlight_regions(
                repository, "A", max_regions=value,  # type: ignore[arg-type]
            )
