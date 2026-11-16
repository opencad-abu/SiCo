"""The source loader contract rejects stale, ambiguous or incomplete inventories."""

import json

import pytest

from utility.skill_loader import check, render
from utility.skill_loader_contract import (
    MANIFEST, contract, declarations, manifest, method_names)
from utility.a_philosophy_gate import run
from utility.tests.gate_fixtures import commit, write
from test_a_philosophy_gate import configuration


def fixture(root):
    data = {"schema_version": 1, "groups": {"sample": {
        "output": "loader.ils", "requires": [], "sources": ["skill/first.il"]}},
        "source_directories": ["skill"], "excluded": {}}
    write(root, str(MANIFEST), json.dumps(data))
    write(root, "skill/first.il", 'procedure(firstRevision() "v1")\nprocedure(first() t)\n')
    write(root, "loader.ils", render(root, "sample"))
    return data


@pytest.mark.parametrize("change", ["body", "revision", "export", "member"])
def test_source_changes_invalidate_only_the_changed_file_contract(tmp_path, change):
    data = fixture(tmp_path)
    before = contract(tmp_path, manifest(tmp_path)["sample"])
    source = tmp_path / "skill/first.il"
    if change == "body":
        source.write_text(source.read_text().replace("first() t", "first() nil"))
    elif change == "revision":
        source.write_text(source.read_text().replace('"v1"', '"v2"'))
    elif change == "export":
        source.write_text(source.read_text() + "procedure(newEntry() t)\n")
    else:
        write(tmp_path, "skill/second.il", "procedure(second() t)\n")
        data["groups"]["sample"]["sources"].append("skill/second.il")
        write(tmp_path, str(MANIFEST), json.dumps(data))
    assert check(tmp_path) and "stale" in check(tmp_path)[0]
    after = contract(tmp_path, manifest(tmp_path)["sample"])
    assert before[0] != after[0]
    if change == "member":
        assert before[1][0] == after[1][0]
    write(tmp_path, "loader.ils", render(tmp_path, "sample"))
    assert check(tmp_path) == []


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unowned", "cycle", "unknown", "escape"])
def test_invalid_dependency_inventory_fails_closed(tmp_path, fault):
    data = fixture(tmp_path)
    group = data["groups"]["sample"]
    if fault == "missing":
        (tmp_path / "skill/first.il").unlink()
    elif fault == "duplicate":
        group["sources"] *= 2
    elif fault == "unowned":
        write(tmp_path, "skill/forgotten.il", "procedure(forgotten() t)\n")
    elif fault == "cycle":
        group["requires"] = ["sample"]
    elif fault == "unknown":
        group["requires"] = ["absent"]
    else:
        group["sources"] = ["../escaped.il"]
    write(tmp_path, str(MANIFEST), json.dumps(data))
    assert check(tmp_path)


def test_declarations_ignore_examples_and_dependency_guards():
    exports, revisions, guards = declarations(
        '; procedure(commented() nil)\n"procedure(quoted() nil)"\n'
        "'(procedure(example() nil))\n"
        'unless(and(boundp(\'dependencyVersion) dependencyVersion=="v1") load("dep.il"))\n'
        'unless(and(boundp(\'ownVersion) ownVersion=="v1")\n'
        'procedure(actual() t)\nprocedure(actualRevision() "v1"))\n')
    assert exports == ["actual", "actualRevision"]
    assert revisions == [("actualRevision", "v1")]
    assert guards == ["ownVersion"]
    with pytest.raises(ValueError, match="unbalanced"):
        declarations('procedure(broken() t')


def test_method_names_are_shared_generics():
    source = (
        'procedure(owned() t)\n'
        'defmethod(sharedBuild ((inst FirstGUI)) t)\n'
        'defmethod(sharedBuild ((inst SecondGUI)) t)\n'
        'defgeneric(singleGeneric (inst))\n'
    )
    assert method_names(source) == ["sharedBuild"]


@pytest.mark.parametrize("deletion", [False, True])
def test_a_gate_rejects_stale_or_deleted_contract(repo, deletion):
    configuration(repo)
    fixture(repo)
    write(repo, "tools/utility/skill_load_ownership.json", json.dumps({
        "schema_version": 1, "lifecycles": [], "loaders": [],
        "source_contract": str(MANIFEST)}))
    commit(repo, "tools", "skill", "loader.ils")
    assert run(repo)["checks"]["source_loader_contract"] == []
    if deletion:
        (repo / MANIFEST).unlink()
    else:
        write(repo, "skill/first.il", 'procedure(firstRevision() "v2")\nprocedure(first() t)\n')
    report = run(repo)
    assert not report["passed"]
    assert report["checks"]["source_loader_contract"]
