"""Mutation checks for declared imports, aliases and scoped UI operations."""

import json
from pathlib import Path

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check, load_manifest
from conftest import write

ROOT = Path(__file__).resolve().parents[2]
UI = next(r for r in load_manifest()["rules"] if r["id"] == "assistant-ui-backend")


@pytest.mark.parametrize("source", [
    "from sico.service.controller import SessionController as C\nC(loop)",
    "import sico.service.controller as module\nfactory = module.SessionController\nfactory(loop)",
    "def first():\n from sico.service.controller import SessionController as C\n return C(loop)",
    "from sico import storage as disk\nconsume(disk)",
    "import socket as net\nsend = net.socket\nsend()",
    "lookup = getattr\nread = lookup(api, 'owner')\nread(token)",
    "read = getattr(api, '_FrontendPort__worker')\nconsume(read)",
    "api._EventFeed__stream",
    "from builtins import open as read\nread(path)",
    "read = api.wait_desktop\nread(1)",
    "api.wait_desktop(False)",
    "receipt.result()",
    "receipt.result(timeout=0)",
    "owner = controller\nowner.rename('name')",
    "def run_window(loop):\n pass",
    "from sico.storage.journal import *",
    "async def first():\n reader = api.read_updates\n await reader(0)",
    "def outer():\n read = api.read_updates\n def inner():\n  read(0)\n return inner",
    "def run(flag):\n if flag:\n  read = api.read_updates\n else:\n  read = api.view\n read(0)",
])
def test_ui_indirect_crossings_fail(source):
    assert violations(source, UI)


@pytest.mark.parametrize("source", [
    "value = api.view(token)\nlabel.setText(value.label)",
    "api.command(token, 'rename', 'new name')",
    "self.renderer._fit_token_usage()",
    "dialog.open()",
    "api.wait_desktop(0)",
    "read = api.wait_desktop\nread(0)",
    "def first():\n action = api.wait_desktop\n action(0)\ndef second(action):\n action(5)",
    "wait = api.wait_desktop\ndef unrelated(wait):\n wait(5)",
    "class Owner:\n wait = api.wait_desktop\n def unrelated(self, wait):\n  wait(5)",
])
def test_ui_values_and_unrelated_local_names_pass(source):
    assert not violations(source, UI)


def test_exceptions_match_exact_source_and_call_shape():
    assert not violations("future.result()", UI, path="tools/sico/python/sico_ui/receipts.py")
    assert violations("future.result(0)", UI, path="tools/sico/python/sico_ui/receipts.py")
    source = "import json\njson.loads(value)"
    assert not violations(source, UI, path="tools/sico/python/sico_ui/elicitation.py")
    assert violations(source, UI, path="tools/sico/python/sico_ui/other.py")
    assert violations("custom.loads(value)", UI, path="tools/sico/python/sico_ui/elicitation.py")


@pytest.mark.parametrize("source", [
    "from .owner import _hidden as value",
    "from pkg.owner import _hidden as value",
    "import pkg.owner as owner\ncopy = owner\ncopy._hidden()",
    "from . import owner as module\nread = getattr(module, '_hidden')\nread()",
    "from pkg.owner import *",
])
def test_private_imports_include_absolute_and_module_aliases(source):
    rule = dict(forbid_private_imports_from=["pkg.owner"])
    assert violations(source, rule, module="pkg.consumer")
    assert not violations("repository._node_names(ids)", rule, module="pkg.consumer")


def internal_fixture(repo):
    write(repo, "pkg/__init__.py")
    write(repo, "pkg/_graph.py", "def public_name(): return 1\ndef _private(): return 2\n")
    write(repo, "pkg/analysis.py", "from ._graph import public_name\npublic_name()\n")
    rule = dict(id="graph", root="pkg", module="pkg._graph", owner="pkg/_graph.py",
                consumers={"pkg/analysis.py": ["public_name"]},
                rationale="Only analysis consumes the internal graph operation.",
                evidence=["pkg/_graph.py:public_name"])
    path = write(repo, "manifest.json", json.dumps(dict(schema_version=1, internal=[rule])))
    return path, rule


def test_internal_api_allows_only_registered_consumer_symbols(repo):
    manifest, _ = internal_fixture(repo)
    assert not check(manifest, root=repo)
    write(repo, "pkg/analysis.py", "from . import _graph as g\nfn = g.public_name\nfn()\n")
    assert not check(manifest, root=repo)
    write(repo, "pkg/new.py", "from pkg._graph import public_name\n")
    assert any("undeclared internal" in e for e in check(manifest, root=repo))
    write(repo, "pkg/new.py", "")
    write(repo, "pkg/analysis.py", "from ._graph import _private\n")
    assert any("_private" in e for e in check(manifest, root=repo))


def test_internal_registry_cannot_hide_invalid_evidence_or_owner(repo):
    manifest, rule = internal_fixture(repo)
    rule["consumers"]["pkg/analysis.py"] = ["missing"]
    manifest.write_text(json.dumps(dict(schema_version=1, internal=[rule])))
    assert any("missing internal owner symbol" in e for e in check(manifest, root=repo))
    rule["evidence"] = ["pkg/_graph.py:removed"]
    manifest.write_text(json.dumps(dict(schema_version=1, internal=[rule])))
    with pytest.raises(ValueError, match="missing.*definition"):
        check(manifest, root=repo)


def test_missing_source_root_and_duplicate_rules_fail(repo):
    manifest = write(repo, "manifest.json", json.dumps(dict(schema_version=1,
                     rules=[dict(id="empty", root="missing")])) )
    with pytest.raises(ValueError, match="missing boundary root"):
        check(manifest, root=repo)
    write(repo, "pkg/a.py", "")
    rule = dict(id="repeated", root="pkg")
    manifest.write_text(json.dumps(dict(schema_version=1, rules=[rule, rule])))
    assert any("duplicate boundary id" in e for e in check(manifest, root=repo))
