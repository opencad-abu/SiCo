"""Audit runner integration and CLI exit codes without product imports."""

import json
from pathlib import Path
import subprocess
import sys

from utility.a_philosophy_gate import run
from conftest import commit, write


SCRIPT = Path(__file__).resolve().parents[1] / "a_philosophy_gate.py"


def configuration(repo):
    write(repo, "pkg/__init__.py")
    write(repo, "pkg/main.py", "def canonical():\n    return 1\n")
    record = dict(
        legacy="old.canonical",
        source="pkg/main.py",
        replacement="pkg/main.py:canonical",
        owner="pkg/main.py",
        consumers=["pkg/main.py"],
        evidence=["pkg/main.py"],
        exit_condition="Remove after caller migration is tested.",
        status="retained",
        allow_exposure=True,
    )
    for name, payload in {
        "a_philosophy_compatibility": dict(schema_version=1, compatibility=[record]),
        "a_philosophy_reviews": dict(schema_version=1, reviews=[]),
        "a_philosophy_size_baseline": dict(schema_version=1, baseline=[]),
        "a_philosophy_inventories": dict(
            schema_version=1,
            inventories=[
                dict(path="native.json", search_paths=["."], entries=["pkg.main"])
            ],
        ),
        "private_import_boundaries": dict(schema_version=1, rules=[]),
        "skill_load_ownership": dict(schema_version=1, loaders=[], lifecycles=[]),
    }.items():
        write(repo, "tools/utility/" + name + ".json", json.dumps(payload))
    write(
        repo,
        "native.json",
        json.dumps(
            dict(
                format="cad.python.native.v1",
                source_root=".",
                modules=[
                    dict(name="pkg.__init__", source="pkg/__init__.py"),
                    dict(name="pkg.main", source="pkg/main.py"),
                ],
            )
        ),
    )
    commit(repo, "tools/utility", "pkg", "native.json")


def test_complete_source_gate_passes_and_writes_private_report(repo):
    configuration(repo)
    output = repo / ".cad/report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPT),
            "--root",
            str(repo),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["passed"]
    assert report["entry_closure"][0]["reachable"] == ["pkg", "pkg.main"]
    assert report["counts"]["hard_over_500_self_owned"] == 0


def test_untracked_test_cannot_escape_gate(repo):
    configuration(repo)
    write(repo, "tests/test_new.py", "\n" * 501)
    report = run(repo)
    assert not report["passed"]
    assert "untracked" in report["checks"]["size"][0]


def test_new_skill_sources_cannot_escape_style_or_ownership_checks(repo):
    configuration(repo)
    write(repo, "skill/new.il", "procedure(new() (list 1))\n")
    commit(repo, "skill/new.il")
    write(repo, "skill/staged.ils", "(list 2)\n")
    subprocess.run(["git", "add", "skill/staged.ils"], cwd=repo, check=True)
    write(repo, "skill/untracked.il", "procedure(untracked() t)\n")
    report = run(repo)
    assert len(report["checks"]["skill_style"]) == 2
    assert len(report["checks"]["skill_load"]) == 2
    assert not report["passed"]


def test_unreviewed_candidate_is_visible_without_inventing_a_hard_violation(repo):
    configuration(repo)
    write(repo, "pkg/main.py", '"""Current facade."""\ndef canonical(): return 1\n')
    report = run(repo)
    assert report["passed"]
    assert report["counts"]["compatibility_candidates"] == 1
    assert report["counts"]["compatibility_reviewed"] == 0
    assert report["compatibility_candidates"][0]["has_registration"]
    assert any("requires review (unreviewed)" in warning for warning in report["warnings"])


def test_failed_closure_and_bad_ast_are_both_reported(repo):
    configuration(repo)
    write(repo, "pkg/main.py", "import pkg.missing\n")
    write(repo, "tests/bad.py", "def broken(:\n")
    report = run(repo)
    assert report["checks"]["inventory"]
    assert report["checks"]["ast"]
    assert report["counts"]["python_parse_errors"] == 1


def test_bad_configuration_exits_two(repo):
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--root", str(repo)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "configuration failed" in result.stderr


def test_committed_new_large_file_cannot_be_hidden_as_existing(repo):
    configuration(repo)
    write(repo, "pkg/new.py", "\n" * 501)
    commit(repo, "pkg/new.py")
    report = run(repo, base_ref="HEAD~1")
    assert any(
        "unbaselined file exceeds" in error for error in report["checks"]["size"]
    )


def test_ci_merge_base_forbids_new_manifest_exemption(repo):
    configuration(repo)
    write(repo, "pkg/new.py", "\n" * 501)
    path = repo / "tools/utility/a_philosophy_size_baseline.json"
    path.write_text(
        json.dumps(
            dict(
                schema_version=1,
                baseline=[
                    dict(
                        path="pkg/new.py",
                        rule="file-lines",
                        lines=501,
                        owner="pkg",
                        remediation="T11",
                        exit_condition="Remove after split.",
                    )
                ],
            )
        )
    )
    commit(repo, "pkg/new.py", "tools/utility/a_philosophy_size_baseline.json")
    report = run(repo, base_ref="HEAD~1")
    assert any("new size exemption" in error for error in report["checks"]["size"])
