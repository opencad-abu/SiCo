"""AS-07 guards share the repository's source, object and size authorities."""

import json
from dataclasses import FrozenInstanceError, fields, replace

import pytest
from agent_boundary_guard import ROOT, contract, product_errors, production_violations, violations
from utility.a_philosophy_baseline import check_baseline
from utility.a_philosophy_sources import base_json, source_rows
from utility.boundary_rules import violations as source_violations

from sico.core.contracts import BoundContext
from sico.service.frontend_session import SessionToken
from sico.service.frontend_startup import FrontendStartup
from sico.service.remote_sessions import RemoteSessions, SessionPublication
from sico.service.service_session_dto import SessionAddress


@pytest.mark.parametrize("source", [
    "from sico.service.project_sessions import ProjectSessionOwner as O\nO(path)",
    "from sico.service.session_recovery import SessionRecovery as R\nR(owner)",
    "from sico.service.frontend_port import FrontendPort\nFrontendPort(worker)",
    "from sico.service.service_client import ServiceClient as C\nC(descriptor)",
    "wire = api._commands\nwire.request('submit', params)",
    "wire = getattr(api, '_queries')\nwire.request('background', params)",
    "wire = window.api.commands\nwire.request('submit', params)",
    "from sico.service.service_entry import run_service\nrun_service(project)",
    "def closeEvent(self, event):\n    stop = self.api.stop_service\n    stop()",
    "def request_quit(self):\n    self.api.end_session(token, 'close', observe=False)",
    "api.publish_session(cached_row)",
    "import os\nterminate = os.kill\nterminate(service.pid, 15)",
    "import importlib\nload = importlib.import_module\nload('sico.service.controller')",
    "__import__('sico.storage.journal')",
])
def test_gui_cannot_bypass_remote_operations_or_own_shutdown(source):
    assert violations(source)


@pytest.mark.parametrize("source", [
    "from sico.service.startup import DesktopStartup\nclass F(DesktopStartup): pass",
    "from .project_sessions import ProjectSessionOwner as O\nO(project)",
    "from sico.providers.config import provider_from_config as build\nbuild(config)",
    "from sico.storage.history import SessionReader\nSessionReader(path)",
])
def test_remote_client_cannot_import_execution_or_storage_owners(source):
    assert source_violations(source, contract("assistant-client-no-execution"),
        path="tools/sico/python/sico/service/frontend_startup.py",
        module="sico.service.frontend_startup")


@pytest.mark.parametrize("rule", ["assistant-ui-backend", "assistant-service-no-qt",
                                 "assistant-client-no-execution"])
def test_production_service_boundaries(rule):
    assert not production_violations(rule)


def test_only_explicit_ui_actions_may_end_sessions_or_stop_service():
    assert not violations("api.stop_service()",
                          "tools/sico/python/sico_ui/service_connection.py")
    path = "tools/sico/python/sico_ui/session_end_operation.py"
    assert not violations("api.end_session(token, operation, observe=False)", path)
    assert violations("api.end_session(token, operation, **options)", path)
    assert violations("api.end_session(token, operation, bypass=True)", path)
    assert not violations("api.close_desktop()\napi.wait_desktop(0)")


def test_startup_base_has_no_inherited_execution_capabilities():
    assert not {"_journal", "_provider", "_sessions", "_close_sessions"} & set(dir(FrontendStartup))


def test_one_immutable_session_publication_owns_all_display_state():
    from types import SimpleNamespace

    descriptor = SimpleNamespace(project_id="a" * 32, service_id="b" * 32)
    sessions = RemoteSessions(descriptor)
    token = SessionToken("live", "c" * 32)
    address = SessionAddress(descriptor.project_id, descriptor.service_id, token)
    context = BoundContext("instance", "generation", "target", {"nested": {"valid": True}})
    row = dict(address=address.record(), context=context, label="label", base="base", model="model",
               resources=False, activity="idle", closing=False, display_name="name")
    assert sessions.publish(row) == token
    published = sessions.get("live")
    assert not product_errors(published)
    assert set(vars(published)) == {field.name for field in fields(SessionPublication)}
    assert "view" not in vars(published) and "row" not in vars(published)
    context.snapshot["nested"]["valid"] = False
    assert published.view.context.snapshot["nested"]["valid"] is True
    with pytest.raises(TypeError):
        published.context.snapshot["nested"]["valid"] = False
    with pytest.raises(FrozenInstanceError):
        published.closing = True
    replacement = replace(token, runtime_id="d" * 32)
    other = dict(row, address=replace(address, session=replacement).record())
    assert sessions.publish(other, catalog=True) == token
    assert sessions.publish(other) == replacement
    sessions.mark_closing(token)
    assert not sessions.get("live").closing
    sessions.mark_closing(replacement)
    sessions.publish(other)
    assert sessions.get("live").closing


def test_service_source_size_uses_existing_baseline_without_new_exemptions():
    roots = ("tools/sico/python/sico/", "tools/sico/python/sico_ui/", "tools/ai/python/tests/")
    rows, errors = source_rows(ROOT)
    assert not errors
    rows = [row for row in rows if row.path.startswith(roots)]
    path = ROOT / "tools/utility/a_philosophy_size_baseline.json"
    current = json.loads(path.read_text())
    previous = base_json(ROOT, "HEAD", path)
    for payload in (current, previous):
        payload["baseline"] = [row for row in payload["baseline"]
                               if row["path"].startswith(roots)]
    errors, warnings, debt = check_baseline(current, rows, previous)
    assert not errors, errors
    # Existing T11 debt stays explicit; this guard cannot certify its remediation.
    assert all(row["lines"] > 500 for row in debt)
