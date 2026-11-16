"""Owned graphical Virtuoso registration and an actual independent Qt connection flow."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_gui_support import isolated_display
from agent_gui_support import wait_until as process_wait
from agent_gui_wait import wait_until
from agent_service_business_fixtures import business_service
from sico.service.home_startup import HomeStartup
from sico.transport.bridge_client import BridgeClient
from sico.transport.host_discovery import list_instances

IC = Path('/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/virtuoso')
ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(os.environ.get('SICO_IC_TESTS') != '1' or not IC.is_file(),
                               reason='Opt-in licensed IC231 platform integration')


@pytest.mark.parametrize("loader", ["tools/sico/skill++/SICO.ils", "scripts/sicoAutoLoad.il"])
def test_graphical_registration_selection_and_disconnect(tmp_path, monkeypatch, loader):
    from types import SimpleNamespace

    from PyQt5.QtWidgets import QApplication
    from sico_ui.home_window import HomeWindow
    from sico_ui.service_connection import attach_service_connection
    from sico_ui.window import AssistantWindow

    stop = tmp_path / 'stop'
    source = tmp_path / 'platform.il'
    source.write_text(
        f'load({json.dumps(str(ROOT / loader))})\n'
        'platformProbeTicks=0\n'
        'procedure(platformProbePoll() platformProbeTicks=platformProbeTicks+1\n'
        f' if(or(isFile({json.dumps(str(stop))}) platformProbeTicks>1200) then exit()\n'
        ' else hiRegTimer("platformProbePoll()" 2)))\n'
        'hiRegTimer("platformProbePoll()" 2)\n')
    with isolated_display(tmp_path) as environment:
        environment.pop('CAD_PYTHON', None)
        environment.update(SICO_HOME=str(ROOT),
                           SICO_PYTHON='/software/pkgs/python/3.9.13/bin/python3',
                           SICO_SYSTEM_LD_LIBRARY_PATH='/software/pkgs/python/3.9.13/lib')
        with (tmp_path / 'virtuoso.stdout').open('w') as output:
            process = subprocess.Popen([str(IC), '-nocdsinit', '-restore', str(source),
                '-log', str(tmp_path / 'CDS.log')], cwd=tmp_path, env=environment,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
            try:
                rows = process_wait(lambda: list_instances(tmp_path), timeout=35)
                assert len(rows) == 1
                assert rows[0]['instance'] == 'v' + str(process.pid)
                monkeypatch.setattr('sico.service.frontend_startup.resolve_provider_config',
                                    lambda *_a, **_k: None)
                with business_service(tmp_path, monkeypatch,
                                      bridge_factory=BridgeClient) as service:
                    args = SimpleNamespace(launch_dir=str(tmp_path), provider_config=None)
                    startup = HomeStartup(args, lambda *_a, **_k: None)
                    opened = []
                    window = HomeWindow(str(tmp_path), tmp_path / 'workspace.ini', opened.append,
                                        args=args)
                    session = None
                    try:
                        window.show()
                        api = startup.ready.result(8)
                        window.ready(api)
                        assert not service.owner.controllers
                        assert not service.provider.seen
                        window.platform_menu.connect()
                        picker = window.platform_menu.picker
                        wait_until(lambda: picker.instances.topLevelItemCount() == 1)
                        picker.instances.setCurrentItem(picker.instances.topLevelItem(0))
                        picker.choose()
                        wait_until(lambda: bool(opened), timeout=10)
                        session = AssistantWindow(opened[0])
                        attach_service_connection(session)
                        session.show()
                        wait_until(lambda: session.presentation.state is not None)
                        wait_until(lambda: '已连接' in session.platform_menu.label.text(),
                                   timeout=8)
                        evidence = Path(os.environ.get('SICO_PLATFORM_EVIDENCE', str(tmp_path)))
                        evidence.mkdir(parents=True, exist_ok=True)
                        window.grab().save(str(evidence / 'home.png'))
                        session.grab().save(str(evidence / 'connected.png'))
                        assert not service.provider.seen
                        session.platform_menu.disconnect()
                        session.platform_menu.confirmation.pick('disconnect')
                        wait_until(lambda: api.is_closing(opened[0].session), timeout=8)
                        wait_until(lambda: not service.owner.controllers, timeout=8)
                        assert process.poll() is None
                        assert list_instances(tmp_path)
                        assert not service.provider.seen
                    finally:
                        if session is not None:
                            session.lifecycle.closing = True
                            session.close()
                            session.deleteLater()
                        window.finish()
                        window.deleteLater()
                        startup.close()
                        assert startup.wait(5)
                        QApplication.instance().processEvents()
            finally:
                stop.touch()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=8)
            assert process.returncode == 0
    assert '*Error*' not in (tmp_path / 'virtuoso.stdout').read_text(errors='replace')


def test_launch_action_starts_registers_and_connects(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PyQt5.QtWidgets import QApplication
    from sico_ui import platform_launcher as launcher_module
    from sico_ui.home_window import HomeWindow
    from sico_ui.service_connection import attach_service_connection
    from sico_ui.window import AssistantWindow

    spawned = []
    real_spawn = launcher_module.spawn_desktop

    def spawn(plan):
        process = real_spawn(plan)
        spawned.append(process)
        return process

    monkeypatch.setattr(launcher_module, 'spawn_desktop', spawn)
    with isolated_display(tmp_path) as environment:
        monkeypatch.setenv('DISPLAY', environment['DISPLAY'])
        monkeypatch.setenv('SICO_HOME', str(ROOT))
        monkeypatch.setenv('SICO_PYTHON', '/software/pkgs/python/3.9.13/bin/python3')
        monkeypatch.setenv('SICO_SYSTEM_LD_LIBRARY_PATH', '/software/pkgs/python/3.9.13/lib')
        monkeypatch.setenv('PATH', str(IC.parent) + os.pathsep + os.environ.get('PATH', ''))
        monkeypatch.setattr('sico.service.frontend_startup.resolve_provider_config',
                            lambda *_a, **_k: None)
        with business_service(tmp_path, monkeypatch, bridge_factory=BridgeClient) as service:
            args = SimpleNamespace(launch_dir=str(tmp_path), provider_config=None)
            startup = HomeStartup(args, lambda *_a, **_k: None)
            opened = []
            window = HomeWindow(str(tmp_path), tmp_path / 'workspace.ini', opened.append,
                                args=args)
            session = None
            try:
                window.show()
                api = startup.ready.result(8)
                window.ready(api)
                window.platform_menu.launch_action.trigger()
                wait_until(lambda: bool(opened), timeout=90)
                assert spawned and spawned[0].poll() is None
                assert list_instances(tmp_path)
                session = AssistantWindow(opened[0])
                attach_service_connection(session)
                session.show()
                wait_until(lambda: session.presentation.state is not None)
                wait_until(lambda: '已连接' in session.platform_menu.label.text(), timeout=10)
                assert not service.provider.seen
            finally:
                if session is not None:
                    session.lifecycle.closing = True
                    session.close()
                    session.deleteLater()
                window.finish()
                window.deleteLater()
                startup.close()
                assert startup.wait(5)
                QApplication.instance().processEvents()
                assert spawned and spawned[0].poll() is None
        for process in spawned:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
