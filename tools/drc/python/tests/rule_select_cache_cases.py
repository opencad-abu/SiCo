"""Rule selector cache regressions."""

from __future__ import annotations
from pathlib import Path
import subprocess
import pytest
import drcpy.rule_expansion as rule_expansion
from drcpy.rule_select import discover_rule_groups


def test_default_cache_dir_uses_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DRC_RULE_GROUP_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "legacy-xdg"))

    assert rule_expansion.default_cache_dir() == tmp_path / ".sico" / "drc-rule-groups"


def test_configured_cache_dir_still_overrides_current_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured-cache"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DRC_RULE_GROUP_CACHE_DIR", str(configured))

    assert rule_expansion.default_cache_dir() == configured


def test_default_cache_dir_honors_frontend_temp_root_after_cwd_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch_temp = tmp_path / "launch" / ".sico"
    launch_temp.parent.mkdir()
    changed = tmp_path / "run"
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.delenv("DRC_RULE_GROUP_CACHE_DIR", raising=False)
    monkeypatch.setenv("SICO_TEMP_DIR", str(launch_temp))

    assert rule_expansion.default_cache_dir() == launch_temp / "drc-rule-groups"


def test_calibre_environment_restores_empty_original_library_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from drcpy.rule_expansion import calibre_environment

    monkeypatch.setenv("LD_LIBRARY_PATH", "/cad/python/lib")
    monkeypatch.setenv("DRC_ORIG_LD_LIBRARY_PATH", "")

    assert calibre_environment()["LD_LIBRARY_PATH"] == ""


@pytest.mark.parametrize(
    ("statement", "relative_path"),
    [
        ('source "quoted.tvf"', "quoted.tvf"),
        ("source {braced.tvf}", "braced.tvf"),
        ('source "$env(TVF_DEP_ROOT)/environment.tvf"', "environment.tvf"),
        ('tvf::INCLUDE "quoted.svrf"', "quoted.svrf"),
        ("tvf::INCLUDE {braced.svrf}", "braced.svrf"),
        (
            'tvf::INCLUDE "$env(TVF_DEP_ROOT)/environment.svrf"',
            "environment.svrf",
        ),
        ('INCLUDE "$SVRF_DEP_ROOT/environment.inc"', "environment.inc"),
    ],
)
def test_tvf_cache_invalidates_when_static_dependency_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    statement: str,
    relative_path: str,
) -> None:
    dependency = tmp_path / relative_path
    dependency.write_text("# dependency version one\n", encoding="utf-8")
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text(f"#! tvf\n{statement}\n", encoding="utf-8")
    monkeypatch.setenv("TVF_DEP_ROOT", str(tmp_path))
    monkeypatch.setenv("SVRF_DEP_ROOT", str(tmp_path))
    calls = 0

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        Path(command[2]).write_text(
            "GROUP EXPANDED expanded_?\nexpanded_1 { COPY M1 }\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr("drcpy.rule_expansion.subprocess.run", fake_run)

    first = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")
    second = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")
    dependency.write_text("# dependency version two\n", encoding="utf-8")
    third = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")

    assert (first.cached, second.cached, third.cached) == (False, True, False)
    assert calls == 2


def test_tvf_cache_skips_dynamic_tcl_dependency_substitutions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variable_dependency = tmp_path / "variable.tvf"
    joined_dependency = tmp_path / "joined.tvf"
    variable_dependency.write_text("# first\n", encoding="utf-8")
    joined_dependency.write_text("# first\n", encoding="utf-8")
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text(
        "#! tvf\n"
        "set helper variable.tvf\n"
        "source $helper\n"
        "source [file join . joined.tvf]\n",
        encoding="utf-8",
    )
    calls = 0

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        Path(command[2]).write_text(
            "GROUP EXPANDED expanded_?\nexpanded_1 { COPY M1 }\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr("drcpy.rule_expansion.subprocess.run", fake_run)

    first = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")
    variable_dependency.write_text("# second\n", encoding="utf-8")
    joined_dependency.write_text("# second\n", encoding="utf-8")
    second = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")

    assert (first.cached, second.cached) == (False, True)
    assert calls == 1
