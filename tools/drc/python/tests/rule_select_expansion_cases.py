"""Rule selector expansion regressions."""

from __future__ import annotations
from pathlib import Path
import subprocess
import pytest
from drcpy.rule_select import discover_rule_groups


def test_discover_rule_groups_expands_tvf_and_caches_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text(
        "#! tvf\nGROUP STATIC check_?\nGROUP G${index} generated_?\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = Path(command[2])
        output.write_text(
            "GROUP STATIC check_?\n"
            "GROUP G1 generated_1 generated_2\n"
            "check_one { COPY M1 }\n"
            "check_two { COPY M2 }\n"
            "generated_1 { COPY M1 }\n"
            "generated_2 { COPY M2 }\n",
            encoding="utf-8",
        )
        assert kwargs["timeout"] == 2.5
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["env"]["LD_LIBRARY_PATH"] == "/eda/calibre/lib"
        assert all(
            name not in kwargs["env"]
            for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR")
        )
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr("drcpy.rule_expansion.subprocess.run", fake_run)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/cadence/python/lib")
    monkeypatch.setenv("DRC_ORIG_LD_LIBRARY_PATH", "/eda/calibre/lib")
    launch_temp = tmp_path / ".cad"
    monkeypatch.setenv("CAD_TEMP_DIR", str(launch_temp))
    for name in ("TMPDIR", "TMP", "TEMP", "SQLITE_TMPDIR"):
        monkeypatch.setenv(name, str(launch_temp))

    first = discover_rule_groups(
        rule_file,
        calibre="calibre-test",
        timeout=2.5,
        cache_dir=tmp_path / "cache",
    )
    second = discover_rule_groups(
        rule_file,
        calibre="calibre-test",
        timeout=2.5,
        cache_dir=tmp_path / "cache",
    )

    assert first.groups == second.groups
    assert first.counts == second.counts
    assert first.groups == ("STATIC", "G1")
    assert first.counts == {"STATIC": 2, "G1": 2}
    assert first.members == {
        "STATIC": ("check_one", "check_two"),
        "G1": ("generated_1", "generated_2"),
    }
    assert first.source == "calibre"
    assert first.error is None
    assert first.cached is False
    assert second.cached is True
    assert calls == [
        ["calibre-test", "-E", calls[0][2], str(rule_file.resolve())]
    ]


def test_svrf_discovers_tvf_include_after_large_plain_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    included = tmp_path / "included.tvf"
    included.write_text("#! tvf\nset suffix 1\n", encoding="utf-8")
    rule_file = tmp_path / "rules.svrf"
    rule_file.write_text(
        "//" + ("x" * 5000) + "\nINCLUDE included.tvf\n",
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

    result = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")

    assert result.groups == ("EXPANDED",)
    assert result.source == "calibre"
    assert calls == 1


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_discover_rule_groups_falls_back_when_calibre_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text("#! tvf\nGROUP STATIC check_?\n", encoding="utf-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, float(kwargs["timeout"]))
        return subprocess.CompletedProcess(command, 2, stdout="license unavailable")

    monkeypatch.setattr("drcpy.rule_expansion.subprocess.run", fake_run)

    result = discover_rule_groups(rule_file, cache_dir=tmp_path / "cache")

    assert result.groups == ("STATIC",)
    assert result.counts == {"STATIC": 0}
    assert result.source == "static"
    assert result.error
    if failure == "timeout":
        assert result.error == "Calibre TVF expansion timed out after 60 seconds"


def test_discover_rule_groups_rejects_invalid_timeout(tmp_path: Path) -> None:
    rule_file = tmp_path / "rules.tvf"
    rule_file.write_text("#! tvf\nGROUP STATIC check_?\n", encoding="utf-8")

    with pytest.raises(ValueError, match="timeout must be positive"):
        discover_rule_groups(rule_file, timeout=0)

    with pytest.raises(ValueError, match="cache directory must not be empty"):
        discover_rule_groups(rule_file, cache_dir="")
