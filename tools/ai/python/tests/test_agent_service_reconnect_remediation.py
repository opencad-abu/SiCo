"""Automatic retry limits reflect failed asynchronous replay results, not request dispatch."""

from concurrent.futures import Future

from agent_gui_wait import wait_until
from agent_service_gui_fixtures import remote_window
from sico_ui.service_connection import attach_service_connection


def test_failed_async_replays_reach_limit_and_manual_retry_can_recover(tmp_path, monkeypatch):
    import sico_ui.service_connection as module
    monkeypatch.setattr(module, 'RETRY_SECONDS', .001)
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        connection = window.service_connection
        original = window.api.prepare_replay
        calls = []
        def failed(*args):
            calls.append(args)
            future = Future()
            future.set_exception(ValueError('replay unavailable'))
            return future
        monkeypatch.setattr(window.api, 'prepare_replay', failed)
        connection.timer.setInterval(20)
        window.binding.fail()
        notifications = []
        monkeypatch.setattr(window.info_app, 'error', lambda *args: notifications.append(args))
        monkeypatch.setattr(window.info_app, 'warning', lambda *args: notifications.append(args))
        wait_until(lambda: len(calls) == module.RETRY_LIMIT, timeout=8)
        wait_until(lambda: not window.page.opening and connection.label.text() == module.GONE)
        assert connection.retry_action.isVisible()
        assert not notifications  # Ten automatic replay failures never open ten popups.
        assert 'replay unavailable' in window.session_notices.messages['connection'][1]
        assert 'replay unavailable' not in connection.label.text()
        for _ in range(5):
            connection.refresh()
        assert len(calls) == module.RETRY_LIMIT
        monkeypatch.setattr(window.api, 'prepare_replay', original)
        connection.reconnect()
        wait_until(connection.attached)
        connection.refresh()
        assert connection._retry.attempts == 1 and not connection.retry_action.isVisible()
        assert 'connection' not in window.session_notices.messages


def test_brief_stream_recoveries_share_budget_and_stability_resets_it(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        connection = window.service_connection
        now = [0]
        connection._retry.clock = lambda: now[0]
        connection._retry.limit = 3
        connection._retry.stable_seconds = 30
        connection._retry.reset()
        connection.timer.setInterval(20)
        for index in range(3):
            window.binding.fail()
            connection.refresh()
            wait_until(connection.attached)
            connection.refresh()
            assert connection._retry.attempts == index + 1
            now[0] = connection._retry.due
        # A successful replay alone cannot give an unstable stream a new budget.
        assert connection._retry.exhausted
        now[0] += 30
        connection.refresh()
        assert connection._retry.attempts == 0


def test_healthy_subscription_retires_pending_startup_without_replaying(tmp_path, monkeypatch):
    from types import SimpleNamespace

    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        connection = window.service_connection
        connection.timer.stop()
        closed = []
        startup = SimpleNamespace(ready=Future(), close=lambda: closed.append(True))
        monkeypatch.setattr("sico.service.service_startup.ServiceStartup", lambda _project: startup)
        activation = window.page.activation
        window.binding.failed = True
        connection.reconnect()
        assert connection._startup is startup
        window.binding.failed = False
        connection.refresh()
        assert connection._startup is None and closed == [True]
        assert window.page.activation == activation and connection.attached()
        connection.refresh()
        assert closed == [True]


def test_control_recovery_is_bounded_across_replay_and_menu_retry_works(tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (_service, window):
        attach_service_connection(window)
        connection, token = window.service_connection, window.page.session
        control = connection.control
        connection.timer.stop()
        control.timer.stop()
        now, calls, owned = [0], [], [False]
        control._retry.clock = lambda: now[0]
        control._retry.reset()
        monkeypatch.setattr(window.api, 'can_control', lambda _token: owned[0])
        monkeypatch.setattr(window.api.control, 'refresh', lambda _token: None)
        from types import SimpleNamespace
        monkeypatch.setattr(window.api.control, 'view',
                            lambda _token: SimpleNamespace(state='recoverable'))

        def ensure(handle):
            calls.append(handle)
            future = Future()
            future.set_exception(RuntimeError('runtime unavailable'))
            return future

        monkeypatch.setattr(window.api.control, 'ensure', ensure)
        for _ in range(5):
            control.refresh()
            control.receipt.poll()
            # Replaying the same session does not reset its control budget.
            window.page.activation += 1
            now[0] += 30
        for _ in range(10):
            control.refresh()
            control.receipt.poll()
        assert calls == [token] * 5 and control.exhausted
        assert '恢复已暂停' in control.label.text()
        connection.refresh()
        assert connection.retry_action.isVisible()
        connection.retry_action.trigger()
        control.receipt.poll()
        assert calls == [token] * 6 and control._retry.attempts == 1
        # A brief successful lease cannot reset consecutive outage history.
        owned[0] = True
        control.refresh()
        now[0] += 1
        control.refresh()
        owned[0] = False
        control.refresh()
        assert control._retry.attempts == 2
        control.receipt.poll()
        owned[0] = True
        control.refresh()
        now[0] += 31
        control.refresh()
        assert control._retry.attempts == 0


def test_detached_event_receipt_fails_display_without_escaping_qt_or_ending_task(
        tmp_path, monkeypatch):
    with remote_window(tmp_path, monkeypatch) as (service, window):
        binding = window.binding
        wait_until(lambda: binding.delivery is not None and binding.delivery.batch is None)
        binding.delivery.request()
        assert binding.delivery.future is not None
        window.api.close_desktop()
        binding.poll()
        assert binding.failed
        assert not service.owner.controllers['live'].closing
