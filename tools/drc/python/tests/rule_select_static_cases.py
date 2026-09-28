"""Rule selector static regressions."""

from __future__ import annotations
from pathlib import Path
import pytest
import drcpy.rule_sources as rule_sources
from drcpy.rule_select import discover_rule_groups, parse_rule_groups, read_rule_groups


def test_parse_rule_groups_handles_svrf_comments_continuations_and_order() -> None:
    source = r"""
// GROUP COMMENTED_OUT ignored_?
/*
GROUP BLOCK_COMMENTED ignored_?
*/
GROUP metal_width met?width
GROUP metal_spacing \
    met?space
GROUP via_checks \   
    via_?
/* leading comment */ GROUP poly_checks poly_?
group lower_case lower_? // trailing comment
GROUP metal_width duplicate_?
GROUP METAL_WIDTH case_duplicate_?
GROUP incomplete
"""

    assert parse_rule_groups(source) == [
        "metal_width",
        "metal_spacing",
        "via_checks",
        "poly_checks",
        "lower_case",
    ]


def test_parse_rule_groups_handles_tvf_and_omits_unresolved_names() -> None:
    source = r"""
#! tvf
namespace import tvf::*
# GROUP TCL_COMMENT ignored_?
tvf::GROUP STATIC_ONE A_?
GROUP {STATIC_TWO} B_?
GROUP "STATIC_THREE" C_?
GROUP G1XM${j} 1XM${j}_?
GROUP G2XM$j 2XM${j}_?
GROUP G[expr_$j] dynamic_?
"""

    assert parse_rule_groups(source) == [
        "STATIC_ONE",
        "STATIC_TWO",
        "STATIC_THREE",
    ]


def test_read_rule_groups_rejects_missing_rule_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="DRC rule file"):
        read_rule_groups(tmp_path / "missing.drc")


def test_read_rule_groups_ignores_undecodable_bytes(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.drc"
    rule_file.write_bytes(b"GROUP FIRST first_?\n\xff\nGROUP SECOND second_?\n")

    assert read_rule_groups(rule_file) == ["FIRST", "SECOND"]


def test_discover_rule_groups_uses_static_parser_for_plain_svrf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_file = tmp_path / "rules.svrf"
    rule_file.write_text("GROUP FIRST first_?\n", encoding="utf-8")
    monkeypatch.setattr(
        "drcpy.rule_expansion.subprocess.run",
        lambda *args, **kwargs: pytest.fail("plain SVRF must not launch Calibre"),
    )

    result = discover_rule_groups(rule_file)

    assert result.groups == ("FIRST",)
    assert result.counts == {"FIRST": 0}
    assert result.source == "static"
    assert result.error is None


def test_static_discovery_flattens_recursive_includes_in_run_directory_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parts = tmp_path / "parts"
    shared = tmp_path / "shared"
    parts.mkdir()
    shared.mkdir()
    rule_file = tmp_path / "rules.svrf"
    rule_file.write_text(
        "GROUP ALL CHILD NEST tail_?\n"
        'INCLUDE "$RULE_INCLUDE_ROOT/parts/first.svrf"\n'
        "GROUP TAIL tail_?\n"
        "tail_1 { COPY M1 }\n",
        encoding="utf-8",
    )
    (parts / "first.svrf").write_text(
        "GROUP CHILD child_?\n"
        "child_1 { COPY M2 }\n"
        "INCLUDE {shared/second.svrf}\n",
        encoding="utf-8",
    )
    (shared / "second.svrf").write_text(
        "GROUP NEST nested_?\n"
        "nested_1 { COPY M3 }\n"
        'INCLUDE "rules.svrf"',
        encoding="utf-8",
    )
    monkeypatch.setenv("RULE_INCLUDE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "drcpy.rule_expansion.subprocess.run",
        lambda *args, **kwargs: pytest.fail("plain SVRF must not launch Calibre"),
    )

    result = discover_rule_groups(rule_file)

    assert result.groups == ("ALL", "CHILD", "NEST", "TAIL")
    assert result.counts == {"ALL": 3, "CHILD": 1, "NEST": 1, "TAIL": 1}
    assert result.members == {
        "ALL": ("child_1", "nested_1", "tail_1"),
        "CHILD": ("child_1",),
        "NEST": ("nested_1",),
        "TAIL": ("tail_1",),
    }
    assert read_rule_groups(rule_file) == ["ALL", "CHILD", "NEST", "TAIL"]


def test_static_discovery_limits_aggregate_include_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    included = tmp_path / "included.svrf"
    included_text = "GROUP CHILD child_?\nchild_1 { COPY M1 }\n"
    included.write_text(included_text, encoding="utf-8")
    rule_file = tmp_path / "rules.svrf"
    root_text = "INCLUDE included.svrf\nGROUP ROOT child_?\n"
    rule_file.write_text(root_text, encoding="utf-8")
    monkeypatch.setattr(
        rule_sources,
        "_MAX_RULE_FILE_BYTES",
        len(root_text.encode()) + len(included_text.encode()) - 1,
    )

    with pytest.raises(ValueError, match="too large"):
        discover_rule_groups(rule_file, expand_tvf=False)


def test_discover_rule_groups_counts_nested_wildcard_and_regex_members(
    tmp_path: Path
) -> None:
    rule_file = tmp_path / "groups.svrf"
    rule_file.write_text(
        "#USING REGEXP GROUP\n"
        "GROUP WIDTH width_?\n"
        "GROUP REGEX \"/space_[12]\"\n"
        "GROUP ALL WIDTH REGEX exact\n"
        "width_1 { COPY M1 }\n"
        "width_2 { COPY M2 }\n"
        "space_1 { COPY M1 }\n"
        "space_3 { COPY M3 }\n"
        "exact { COPY M4 }\n",
        encoding="utf-8",
    )

    result = discover_rule_groups(rule_file, expand_tvf=False)

    assert result.counts == {"WIDTH": 2, "REGEX": 1, "ALL": 4}
    assert result.members == {
        "WIDTH": ("width_1", "width_2"),
        "REGEX": ("space_1",),
        "ALL": ("width_1", "width_2", "space_1", "exact"),
    }


def test_group_wildcard_treats_regex_metacharacters_as_literals(
    tmp_path: Path,
) -> None:
    rule_file = tmp_path / "literal-pattern.svrf"
    rule_file.write_text(
        "GROUP PLUS M+_?\n"
        "GROUP QUESTION M?_?\n"
        "M+_X { COPY M1 }\n"
        "M1 { COPY M1 }\n"
        "M1_X { COPY M1 }\n"
        "M12_X { COPY M1 }\n",
        encoding="utf-8",
    )

    result = discover_rule_groups(rule_file, expand_tvf=False)

    assert result.counts == {"PLUS": 1, "QUESTION": 3}
