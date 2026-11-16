"""Mutations prove static checks catch indirect frontend backend access."""

import pytest
from agent_boundary_guard import production_violations, violations


@pytest.mark.parametrize("source", [
    "from sico.service.controller import SessionController as C\nC(loop)",
    "import sico.service.sessions as s\ns.DesktopSessions(loop)",
    "build = SessionJournal\nbuild(path, session)",
    "read = owner.journal.events\nread()",
    "read = getattr(owner, 'journal')\nread.events()",
    "handle._owner.rename('name')",
    "api.owner(token)",
    "lookup = getattr(api, 'owner')\nlookup(token)",
    "api.sessions.controllers.clear()",
    "from sico.service.session_tokens import SessionTokens\nSessionTokens(s)",
    "read = source.read_updates\nread(0)",
    "wait = api.wait_desktop\nwait()",
    "def run_window(loop):\n    pass",
])
def test_indirect_violation_is_detected(source):
    assert violations(source)


@pytest.mark.parametrize("source", [
    "view = api.view(handle)\nlabel.setText(view.label)",
    "future = api.command(handle, 'submit', text)",
    "settings.setValue('geometry', window.saveGeometry())",
    "query = WorkbenchQuery(activation=1, query_id='1')",
    "api.wait_desktop(0)",
])
def test_presentation_and_async_submission_are_allowed(source):
    assert not violations(source)


def test_production_ui_has_no_backend_construction_or_journal_escape():
    assert not production_violations("assistant-ui-backend")


def test_service_core_does_not_import_qt():
    assert not production_violations("assistant-service-no-qt")
