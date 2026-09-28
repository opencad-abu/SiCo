"""Alias drift, copies and missing targets cannot satisfy registered identity."""

import pytest

from utility.a_philosophy_exports import ExportResolver, check_export_groups, surface_digest
from conftest import write


@pytest.fixture
def exports(repo):
    write(repo, "src/pkg/owner.py", "class Result:\n    pass\nVALUE = {}\n")
    write(repo, "src/pkg/api.py", "from .owner import Result as OldResult, VALUE\nAlias = OldResult\n")
    record = dict(legacy="pkg.api aliases", source="src/pkg/api.py",
                  replacement="src/pkg/owner.py", exports={"Alias": "Result", "VALUE": "VALUE"})
    return repo, record


def test_relative_alias_chain_and_constant_identity(exports):
    root, row = exports
    assert check_export_groups(root, [row], ["src"]) == []


@pytest.mark.parametrize("body", [
    "from .owner import Result\nAlias = object()\nVALUE = {}\n",
    "from .owner import Result as Alias, VALUE as original\nVALUE = dict(original)\n",
    "from .owner import Absent as Alias, VALUE\n",
    "Alias = Again\nAgain = Alias\nVALUE = {}\n",
    "from .owner import Result as Alias, VALUE\nif enabled:\n    Alias = object()\n",
])
def test_replacement_copy_missing_target_and_cycle_fail(exports, body):
    root, row = exports
    write(root, row["source"], body)
    assert check_export_groups(root, [row], ["src"])


def test_absolute_module_attribute_and_package_import_aliases(exports):
    root, row = exports
    for body in ("import pkg.owner as actual", "from . import owner as actual"):
        write(root, row["source"], body + "\nAlias=actual.Result\nVALUE=actual.VALUE\n")
        assert check_export_groups(root, [row], ["src"]) == []


def test_ambiguous_search_roots_do_not_pick_an_owner(exports):
    root, row = exports
    write(root, "else/pkg/owner.py", "class Result: pass\nVALUE = {}\n")
    write(root, row["source"], "from pkg.owner import Result as Alias, VALUE\n")
    assert any("ambiguous" in e for e in check_export_groups(root, [row], ["src", "else"]))


def test_conditional_literals_are_owned_but_conditional_aliases_are_not_guessed(exports):
    root, _ = exports
    write(root, "src/pkg/conditional.py", "try:\n    FLAG='one'\nexcept ValueError:\n    FLAG='two'\n")
    resolver = ExportResolver(root, ["src"])
    assert resolver.resolve("src/pkg/conditional.py", "FLAG") == ("src/pkg/conditional.py", "FLAG")
    write(root, "src/pkg/dynamic.py", "if enabled:\n    from .owner import Result\n")
    with pytest.raises(ValueError, match="missing explicit export"):
        resolver.resolve("src/pkg/dynamic.py", "Result")


def test_duplicate_export_and_empty_mapping_fail(exports):
    root, row = exports
    assert any("duplicate compatibility export" in e for e in check_export_groups(root, [row, row], ["src"]))
    assert check_export_groups(root, [{**row, "exports": {}}], ["src"])


def test_surface_tracks_new_alias_and_signature_but_not_body_or_format():
    source = "from .owner import Result\ndef run(value):\n    return value\n"
    fingerprint = surface_digest(source)
    assert surface_digest(source.replace("return value", "return None")) == fingerprint
    assert surface_digest("# comment\n" + source) == fingerprint
    assert surface_digest(source + "Alias = Result\n") != fingerprint
    assert surface_digest(source.replace("run(value)", "run(value, mode=None)")) != fingerprint
    assert surface_digest("class View:\n    def refresh(self): pass\n") != surface_digest(
        "class View:\n    def refresh(self, cursor): pass\n"
    )
    assert surface_digest("if enabled:\n    from .a import Owner\n") != surface_digest(
        "if enabled:\n    from .b import Owner\n"
    )
