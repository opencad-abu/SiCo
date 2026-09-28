"""Rule selection protocol regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
from drcpy.rule_selection import RuleSelection, read_initial_selection, write_selection


def test_initial_selection_tsv_is_case_insensitive_and_validated(
    tmp_path: Path,
) -> None:
    initial = tmp_path / "initial.tsv"
    initial.write_text(
        "# existing selection\n"
        "GROUP\tMetal\n"
        "group\tMETAL\n"
        "CHECK\tV1.SPACE\n"
        "CHECK\tv1.space\n",
        encoding="ascii",
    )

    assert read_initial_selection(initial) == RuleSelection(("Metal",), ("V1.SPACE",))

    initial.write_text("UNKNOWN\tM1\n", encoding="ascii")
    with pytest.raises(ValueError, match="Unsupported Rule Select record"):
        read_initial_selection(initial)


def test_atomic_selection_output_uses_apply_protocol(tmp_path: Path) -> None:
    output = tmp_path / "result.tsv"
    write_selection(output, RuleSelection(("METAL",), ("V1.SPACE",)))

    assert output.read_text(encoding="ascii") == (
        "#status\tapplied\nGROUP\tMETAL\nCHECK\tV1.SPACE\n"
    )
    assert not list(tmp_path.glob(f".{output.name}.*.tmp"))


def test_selection_output_does_not_create_unrequested_parent(tmp_path: Path) -> None:
    output = tmp_path / "missing" / "result.tsv"

    with pytest.raises(FileNotFoundError, match="output directory"):
        write_selection(output, RuleSelection(("METAL",), ()))

    assert not output.parent.exists()


def test_selection_output_refuses_a_preexisting_result(tmp_path: Path) -> None:
    output = tmp_path / "result.tsv"
    output.write_text("stale\n", encoding="ascii")

    with pytest.raises(FileExistsError, match="already exists"):
        write_selection(output, RuleSelection(("METAL",), ()))

    assert output.read_text(encoding="ascii") == "stale\n"
