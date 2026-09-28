"""Import graph regression cases that the earlier inventory loops missed."""

import json

import pytest

from utility.a_philosophy_inventory import check_inventories


def test_relative_multiline_package_initializers_and_cross_package_closure(
    closure_repo,
):
    repo, make = closure_repo
    manifest = make(
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "import shared.init_dependency\n",
            "pkg/main.py": "from .sub import (\n    worker,\n)\nfrom .values import helper\n",
            "pkg/sub/worker.py": "from ..values import helper\nimport shared.worker\n",
            "pkg/values.py": "def helper():\n    pass\n",
            "pkg/helper.py": 'raise AssertionError("not imported")\n',
            "shared/__init__.py": "",
            "shared/worker.py": "",
            "shared/init_dependency.py": "",
        }
    )
    errors, reports = check_inventories(repo, manifest)
    assert not errors
    assert set(reports[0]["reachable"]) == {
        "pkg",
        "pkg.main",
        "pkg.sub",
        "pkg.sub.worker",
        "pkg.values",
        "shared",
        "shared.worker",
        "shared.init_dependency",
    }


@pytest.mark.parametrize(
    "source",
    [
        "from . import missing\n",
        "from pkg import missing\n",
        "from pkg.missing import thing\n",
        "import pkg.missing\n",
        'import importlib as loader\nloader.import_module(".missing", package="pkg")\n',
        'from importlib import import_module as load\nload("pkg.missing")\n',
    ],
)
def test_existing_local_dependency_missing_from_inventory_fails(closure_repo, source):
    repo, make = closure_repo
    manifest = make(
        {"pkg/__init__.py": "", "pkg/main.py": source, "pkg/missing.py": ""},
        registered=["pkg/__init__.py", "pkg/main.py"],
    )
    errors, _ = check_inventories(repo, manifest)
    assert any(
        "reachable dependency missing from inventory: pkg.missing" in e for e in errors
    )


def test_cross_package_missing_inventory_and_nested_missing_source(closure_repo):
    repo, make = closure_repo
    manifest = make(
        {
            "pkg/__init__.py": "",
            "pkg/main.py": "import shared.worker\nimport pkg.sub.absent\n",
            "pkg/sub/__init__.py": "",
            "shared/__init__.py": "",
            "shared/worker.py": "",
        },
        registered=["pkg/__init__.py", "pkg/main.py", "pkg/sub/__init__.py"],
    )
    errors, _ = check_inventories(repo, manifest)
    assert any("missing from inventory: shared.worker" in e for e in errors)
    assert any("local import source missing: pkg.sub.absent" in e for e in errors)


def test_complete_package_finds_unreachable_new_module(closure_repo):
    repo, make = closure_repo
    manifest = make(
        {"pkg/__init__.py": "", "pkg/main.py": "", "pkg/new.py": ""},
        registered=["pkg/__init__.py", "pkg/main.py"],
        complete=["pkg"],
    )
    errors, _ = check_inventories(repo, manifest)
    assert any("package source missing from inventory: pkg.new" in e for e in errors)


def test_duplicate_sources_and_wrong_module_path_fail(closure_repo):
    repo, make = closure_repo
    manifest = make({"pkg/__init__.py": "", "pkg/main.py": "", "pkg/wrong.py": ""})
    path = repo / "native.json"
    data = json.loads(path.read_text())
    data["modules"][-1]["source"] = "pkg/main.py"
    path.write_text(json.dumps(data))
    errors, _ = check_inventories(repo, manifest)
    assert any("duplicate inventory source" in e for e in errors)
    assert any("inventory name/source mismatch" in e for e in errors)


def test_dynamic_nonliteral_import_is_a_review_clue(closure_repo):
    repo, make = closure_repo
    manifest = make(
        {
            "pkg/__init__.py": "",
            "pkg/main.py": "from importlib import import_module\nimport_module(selected)\n",
        }
    )
    errors, reports = check_inventories(repo, manifest)
    assert not errors
    assert reports[0]["dynamic_import_review"][0]["line"] == 2


def test_bad_reachable_ast_reports_failure(closure_repo):
    repo, make = closure_repo
    manifest = make({"pkg/__init__.py": "", "pkg/main.py": "def broken(:\n"})
    errors, _ = check_inventories(repo, manifest)
    assert any("closure check failed" in e for e in errors)


@pytest.mark.parametrize(
    "payload", [[], {}, {"schema_version": 2}, {"schema_version": 1, "inventories": []}]
)
def test_invalid_closure_manifest_cannot_silently_pass(tmp_path, payload):
    errors, _ = check_inventories(tmp_path, payload)
    assert errors
