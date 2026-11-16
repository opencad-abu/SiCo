"""Service record operations reject live/unresolved runtimes and allow ended-session creation."""

import threading
from contextlib import ExitStack

import pytest
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service, open_session
from agent_service_gui_fixtures import remote_window
from test_agent_service_control import attach
from sico.service.session_end import SessionEnd
from sico.storage.history import SessionReader


def end(api, token, service):
    api.end_session(token, 'end-record-test').result(5)
    wait_until(lambda: token.session_id not in service.owner.controllers)


def test_delete_archive_persists_tombstone_without_a_runtime(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        token = window.page.session
        with pytest.raises(ValueError, match='先结束并归档'):
            window.api.delete_session(token.session_id).result(5)
        end(window.api, token, service)
        window.api.delete_session(token.session_id).result(5)
        facts = service.dispatch.frontend.recovery.inspect(token.session_id)
        assert facts['state'] == 'ended'
        assert not service.owner.controllers
        assert service.dispatch.frontend.workspace.catalog.snapshot()['availability']['live'] == '已删除'
        reader = SessionReader(tmp_path / '.sico/ai/agent', token.session_id)
        assert any(row['kind'] == 'codex.thread.deleted' for row in reader.events())
        window.api.delete_session(token.session_id).result(5)  # Tombstone is idempotent.


def test_new_window_can_delete_published_history_without_old_control(tmp_path, monkeypatch):
    with business_service(tmp_path, monkeypatch) as service, ExitStack() as stack:
        open_session(service)
        api, token = attach(stack, service, tmp_path)
        api.control.request(token, 'acquire').result(5)
        end(api, token, service)
        from sico.service.remote_frontend import RemoteFrontend
        other = RemoteFrontend(service.descriptor, tmp_path, 'live')
        stack.callback(other.close_desktop)
        other.recovery.open_history('live').result(5)
        other.delete_session('live').result(5)
        assert service.dispatch.frontend.workspace.catalog.snapshot()['availability']['live'] == '已删除'


def test_closing_record_cannot_be_deleted_until_drain_finishes(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = SessionEnd._drain
    def delayed(ending, *args):
        entered.set()
        assert release.wait(10)
        return original(ending, *args)
    with remote_window(tmp_path, monkeypatch) as (service, window):
        monkeypatch.setattr(SessionEnd, '_drain', delayed)
        token = window.page.session
        try:
            window.api.end_session(token, 'slow-record').result(5)
            wait_until(entered.is_set)
            with pytest.raises(ValueError, match='先结束并归档'):
                window.api.delete_session('live').result(5)
        finally:
            release.set()
        wait_until(lambda: not service.owner.controllers)
        window.api.delete_session('live').result(5)


def test_unreleased_end_evidence_blocks_deletion(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        end(window.api, window.page.session, service)
        directory = tmp_path / '.sico/ai/agent/sessions/live'
        releases = list((directory / 'end-releases').glob('*.json'))
        if (directory / 'end-release.json').exists():
            releases.append(directory / 'end-release.json')
        assert releases
        for release in releases:
            release.unlink()
        with pytest.raises(ValueError, match='未决工作'):
            window.api.delete_session('live').result(5)
        assert not service.dispatch.frontend.recovery._facts('live').deleted


def test_last_session_can_be_ended_then_new_session_created_and_submitted(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        old = window.page.session
        context = window.page.view.context
        end(window.api, old, service)
        window.new_session()
        wait_until(lambda: window.page.session != old and not window.page.opening)
        new = window.page.session
        assert new.session_id != old.session_id and new.runtime_id != old.runtime_id
        assert window.api.view(new).context == context and window.api.can_control(new)
        window.api.command(new, 'submit', 'new session task').result(5)
        assert service.owner.controllers[new.session_id].wait(5)
        assert service.provider.seen == ['new session task']
        assert old.session_id not in service.owner.controllers


def test_retired_creation_source_cannot_change_target(tmp_path, monkeypatch):
    from dataclasses import replace
    with remote_window(tmp_path, monkeypatch) as (service, window):
        old = window.page.session
        end(window.api, old, service)
        with pytest.raises(ValueError, match='创建来源'):
            window.api.create_session(replace(window.page.view.context, target_id='different')).result(5)
        with pytest.raises(ValueError, match='目标已变化'):
            window.api.command(old, 'create',
                replace(window.page.view.context, target_id='different')).result(5)
        assert not service.owner.controllers


def test_rename_an_ended_python_record(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        end(window.api, window.page.session, service)
        window.api.rename_session('live', 'Archived design review').result(5)
        rows = service.dispatch.frontend.workspace.catalog.snapshot()['rows']
        assert next(row for row in rows if row['id'] == 'live')['name'] == 'Archived design review'


def test_row_menu_archives_then_deletes_last_record_from_navigation(tmp_path, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMenu
    from sico_ui.service_connection import attach_service_connection
    from sico_ui import navigation_menu

    with remote_window(tmp_path, monkeypatch) as (service, window):
        attach_service_connection(window)
        window.show()
        nav = window.navigation
        def row():
            nav.refresh()
            return next((item for item in nav._session_items()
                         if item.data(0, Qt.UserRole) == 'live'), None)
        wait_until(lambda: row() is not None)
        chosen = []
        class Menu(QMenu):
            def exec_(self, *_args):
                labels = [action.text() for action in self.actions()]
                chosen.append(labels)
                return next(action for action in self.actions()
                            if action.text() in {'结束并归档', '删除'})
        monkeypatch.setattr(navigation_menu, 'QMenu', Menu)
        item = row()
        nav._menu(item.treeWidget().visualItemRect(item).center(), item.treeWidget())
        box = window.service_connection.ending.dialog
        assert box is not None and '删除' not in chosen[-1]
        box.button("accept").click()
        wait_until(lambda: row() is not None and row().treeWidget() is nav.list
                   and '（已结束）' in row().text(0))
        monkeypatch.setattr("sico_ui.navigation_actions.confirm",
                            lambda *_args, **_kwargs: True)
        item = row()
        nav._menu(nav.list.visualItemRect(item).center(), nav.list)
        assert '删除' in chosen[-1] and '结束并归档' not in chosen[-1]
        wait_until(lambda: service.dispatch.frontend.workspace.catalog.snapshot()
                   ['availability'].get('live') == '已删除')
        wait_until(lambda: row() is None)
        assert not service.owner.controllers
        window.new_session()
        wait_until(lambda: window.page.session.session_id != 'live' and not window.page.opening)


def test_snapshot_only_record_can_be_renamed_then_deleted(tmp_path, monkeypatch):
    from sico.service.remote_frontend import RemoteFrontend
    with business_service(tmp_path, monkeypatch) as service:
        open_session(service)
        # A session never controlled or submitted has a snapshot but no events.
        service.owner.close_session('live')
        api = RemoteFrontend(service.descriptor, tmp_path, 'live')
        try:
            api.rename_session('live', 'Unused archive').result(5)
            rows = service.dispatch.frontend.workspace.catalog.snapshot()['rows']
            assert next(row for row in rows if row['id'] == 'live')['name'] == 'Unused archive'
            api.delete_session('live').result(5)
            assert service.dispatch.frontend.recovery._facts('live').deleted
        finally:
            api.close_desktop()
            wait_until(lambda: api.wait_desktop(0))
