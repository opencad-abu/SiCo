from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from drcpy.cli import main
from drcpy.rule_select import RuleGroupInfo


def test_rule_groups_command_lists_static_groups(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule_file = tmp_path / "rules.drc"
    rule_file.write_text(
        "GROUP FIRST first_?\nGROUP G${index} generated_?\nGROUP SECOND second_?\n",
        encoding="utf-8",
    )

    assert main(["rule-groups", "--static", str(rule_file)]) == 0
    captured = capsys.readouterr()
    assert captured.out == "FIRST\nSECOND\n"


def test_rule_groups_command_can_emit_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text("#! tvf\ntvf::GROUP STATIC check_?\n", encoding="utf-8")

    assert main(["rule-groups", "--static", str(rule_file), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "cached": False,
        "counts": {"STATIC": 0},
        "error": None,
        "groups": ["STATIC"],
        "members": {"STATIC": []},
        "source": "static",
    }


def test_rule_groups_command_reports_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["rule-groups", "--static", str(tmp_path / "missing.drc")]) == 1
    captured = capsys.readouterr()
    assert "DRC rule file" in captured.err


def test_rule_groups_command_writes_tsv_for_skill_ui(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.svrf"
    output = tmp_path / "groups.tsv"
    rule_file.write_text(
        "GROUP WIDTH width_?\nwidth_1 { COPY M1 }\nwidth_2 { COPY M2 }\n",
        encoding="utf-8",
    )

    assert (
        main(
            [
                "rule-groups",
                "--static",
                "--tsv",
                "--output",
                str(output),
                str(rule_file),
            ]
        )
        == 0
    )
    assert output.read_text(encoding="utf-8") == "WIDTH\t2\n"


def test_rule_groups_command_writes_skill_metadata(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.svrf"
    output = tmp_path / "groups.tsv"
    rule_file.write_text("GROUP WIDTH width_?\nwidth_1 { COPY M1 }\n", encoding="utf-8")

    assert (
        main(
            [
                "rule-groups",
                "--static",
                "--skill-tsv",
                "--output",
                str(output),
                str(rule_file),
            ]
        )
        == 0
    )
    assert output.read_text(encoding="utf-8") == (
        "#source\tstatic\n#cached\tfalse\n#error\t\nWIDTH\t1\twidth_1\n"
    )


def test_rule_groups_skill_metadata_escapes_fallback_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_file = tmp_path / "rules.tvf"
    output = tmp_path / "groups.tsv"
    rule_file.write_text("#! tvf\n", encoding="utf-8")
    monkeypatch.setattr(
        "drcpy.cli.discover_rule_groups",
        lambda *args, **kwargs: RuleGroupInfo(
            ("WIDTH",),
            {"WIDTH": 1},
            "static",
            "line one\nline\ttwo",
            members={"WIDTH": ("width_1",)},
        ),
    )

    assert (
        main(["rule-groups", "--skill-tsv", "--output", str(output), str(rule_file)])
        == 0
    )
    assert output.read_text(encoding="utf-8") == (
        "#source\tstatic\n#cached\tfalse\n"
        "#error\tline one\\nline\\ttwo\nWIDTH\t1\twidth_1\n"
    )


def test_rule_groups_command_rejects_conflicting_formats() -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["rule-groups", "--json", "--tsv", "rules.svrf"])


def test_rule_groups_command_does_not_create_output_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rule_file = tmp_path / "rules.svrf"
    rule_file.write_text("GROUP WIDTH width_?\n", encoding="utf-8")
    output = tmp_path / "missing" / "groups.tsv"

    assert (
        main(
            [
                "rule-groups",
                "--static",
                "--tsv",
                "--output",
                str(output),
                str(rule_file),
            ]
        )
        == 1
    )
    assert not output.parent.exists()
    assert "No such file or directory" in capsys.readouterr().err


def test_rule_groups_command_atomically_replaces_existing_output(
    tmp_path: Path,
) -> None:
    rule_file = tmp_path / "rules.svrf"
    rule_file.write_text("GROUP WIDTH width_?\n", encoding="utf-8")
    output = tmp_path / "groups.tsv"
    output.write_text("stale\n", encoding="utf-8")

    assert (
        main(
            [
                "rule-groups",
                "--static",
                "--tsv",
                "--output",
                str(output),
                str(rule_file),
            ]
        )
        == 0
    )
    assert output.read_text(encoding="utf-8") == "WIDTH\t0\n"
    assert not list(tmp_path.glob(".groups.tsv.*.tmp"))


def test_rule_select_gui_command_forwards_launcher_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_file = tmp_path / "rules.svrf"
    initial = tmp_path / "initial.tsv"
    output = tmp_path / "result.tsv"
    called: dict[str, object] = {}

    fake_module = ModuleType("drcpy.rule_select_gui")

    def fake_run_gui(rule_file_arg, **kwargs):
        called["rule_file"] = rule_file_arg
        called.update(kwargs)
        return 7

    fake_module.run_gui = fake_run_gui  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "drcpy.rule_select_gui", fake_module)

    assert (
        main(
            [
                "rule-select-gui",
                "--initial",
                str(initial),
                "--output",
                str(output),
                "--static",
                "--parent-pid",
                "12345",
                str(rule_file),
            ]
        )
        == 7
    )
    assert called == {
        "rule_file": str(rule_file),
        "initial_path": str(initial),
        "output_path": str(output),
        "calibre": None,
        "cache_dir": None,
        "timeout": 60.0,
        "expand_tvf": False,
        "parent_pid": 12345,
    }


def test_rule_select_gui_command_requires_output() -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["rule-select-gui", "rules.svrf"])
