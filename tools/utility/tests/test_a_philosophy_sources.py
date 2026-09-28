"""Source discovery and per-file ratchets across Git stages."""

import copy
import subprocess

import pytest

from utility.a_philosophy_sources import source_rows
from utility.a_philosophy_baseline import check_baseline
from conftest import commit, write


def baseline(path, lines):
    return dict(
        schema_version=1,
        baseline=[
            dict(
                path=path,
                lines=lines,
                rule="file-lines",
                owner="pkg",
                remediation="T11-test",
                exit_condition="Split to <=500 lines.",
            )
        ],
    )


def test_staged_untracked_languages_and_vendor_are_separate(repo):
    write(repo, "pkg/old.py")
    commit(repo, "pkg/old.py")
    write(repo, "pkg/new.cpp", "\n" * 501)
    write(repo, "tests/test_new.py", "\n" * 501)
    write(repo, "pkg/callback.ils", "\n" * 301)
    write(repo, "pkg/form.il.src", "\n" * 3)
    write(repo, "pkg/_vendor/lib/parser.py", "\n" * 793)
    write(repo, "pkg/reference/ignored.py", "bad python!")
    write(repo, ".cad/ignored.py", "bad python!")
    write(repo, "pkg/build/ignored.py", "bad python!")
    subprocess.run(["git", "add", "--", "pkg/new.cpp"], cwd=repo, check=True)
    rows, errors = source_rows(repo)
    assert not errors
    found = {row.path: row for row in rows}
    assert len(found) == 6
    assert found["pkg/new.cpp"].status == "staged-new"
    assert found["tests/test_new.py"].status == "untracked"
    assert found["tests/test_new.py"].test
    assert found["pkg/_vendor/lib/parser.py"].vendored
    errors, warnings, hard = check_baseline(dict(schema_version=1, baseline=[]), rows)
    assert len(errors) == 2
    assert len(warnings) == 1
    assert len(hard) == 2


@pytest.mark.parametrize(
    "mutation, expected",
    [
        ("grow", "regressed"),
        ("shrink", "lower baseline"),
        ("resolved", "remove resolved"),
        ("swap", "unbaselined"),
    ],
)
def test_baseline_cannot_trade_one_problem_for_another(repo, mutation, expected):
    write(repo, "old.py", "\n" * 600)
    commit(repo, "old.py")
    policy = baseline("old.py", 600)
    if mutation == "grow":
        write(repo, "old.py", "\n" * 601)
    elif mutation == "shrink":
        write(repo, "old.py", "\n" * 550)
    elif mutation == "resolved":
        write(repo, "old.py", "\n" * 500)
    else:
        (repo / "old.py").unlink()
        write(repo, "new.py", "\n" * 600)
    rows, _ = source_rows(repo)
    errors, _, _ = check_baseline(policy, rows)
    assert any(expected in error for error in errors)


def test_manifest_ceiling_cannot_increase_even_when_file_does_not(repo):
    write(repo, "old.py", "\n" * 600)
    commit(repo, "old.py")
    old = baseline("old.py", 600)
    current = copy.deepcopy(old)
    current["baseline"][0]["lines"] = 700
    rows, _ = source_rows(repo)
    errors, _, _ = check_baseline(current, rows, old)
    assert any("ceiling increased" in error for error in errors)


def test_new_baseline_exemption_is_rejected(repo):
    write(repo, "old.py", "\n" * 600)
    commit(repo, "old.py")
    rows, _ = source_rows(repo)
    errors, _, _ = check_baseline(
        baseline("old.py", 600), rows, dict(schema_version=1, baseline=[])
    )
    assert any("new size exemption" in error for error in errors)


def test_staged_new_file_cannot_be_baselined(repo):
    write(repo, "old.py")
    commit(repo, "old.py")
    write(repo, "new.py", "\n" * 600)
    subprocess.run(["git", "add", "--", "new.py"], cwd=repo, check=True)
    rows, _ = source_rows(repo)
    errors, _, _ = check_baseline(baseline("new.py", 600), rows)
    assert any("new source cannot" in error for error in errors)


def test_vendor_cannot_be_self_owned_baseline(repo):
    write(repo, "pkg/_vendor/parser.py", "\n" * 793)
    commit(repo, "pkg")
    rows, _ = source_rows(repo)
    errors, _, hard = check_baseline(baseline("pkg/_vendor/parser.py", 793), rows)
    assert any("vendored source" in error for error in errors)
    assert not hard


def test_vendor_directory_name_cannot_create_a_new_exemption(repo):
    from utility.a_philosophy_sources import check_vendors

    write(repo, "pkg/_vendor/parser.py", "\n" * 793)
    rows, _ = source_rows(repo)
    assert check_vendors(repo, rows, {}) == [
        "unreviewed vendored source: pkg/_vendor/parser.py"
    ]
    write(repo, "LICENSE", "attribution")
    pins = dict(
        vendored_sources=[
            dict(
                path=rows[0].path,
                sha256=rows[0].sha256,
                owner="test library",
                license="LICENSE",
            )
        ]
    )
    assert not check_vendors(repo, rows, pins)
    write(repo, rows[0].path, "\n" * 794)
    changed, _ = source_rows(repo)
    assert any("pin changed" in e for e in check_vendors(repo, changed, pins))


def test_symlink_cannot_hide_large_source(repo):
    write(repo, "large.py", "\n" * 600)
    (repo / "hidden.py").symlink_to("large.py")
    _, errors = source_rows(repo)
    assert errors == ["source symlink requires review: hidden.py"]
