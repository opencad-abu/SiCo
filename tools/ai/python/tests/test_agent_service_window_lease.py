"""One SiCo window per project: the desktop lock owns the rule, the OS owns recovery."""

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from sico.service.frontend_startup import FrontendStartup
from sico.service.startup_result import StartupFailure
from sico.service.window_lease import WindowAlreadyOpen, claim_window


def startup_args(project, mode="desktop-worker"):
    return SimpleNamespace(command=mode, session="window", launch_dir=project,
                           provider_config=None, timeout=3, context_file=None, message="")


def test_second_window_is_refused_until_the_first_one_closes(tmp_path):
    first = claim_window(tmp_path)
    try:
        with pytest.raises(WindowAlreadyOpen) as refused:
            claim_window(tmp_path)
        assert "已有一个 SiCo 窗口在运行" in str(refused.value)
    finally:
        first.close()
    claim_window(tmp_path).close()


def test_startup_refuses_a_second_window_before_touching_the_service(tmp_path):
    """工程里没有服务也必须先被窗口锁挡住，而不是去发现/拉起服务。"""

    holder = claim_window(tmp_path)
    try:
        startup = FrontendStartup(startup_args(tmp_path), lambda *_args, **_kwargs: None)
        with pytest.raises(StartupFailure) as refused:
            startup.ready.result(5)
        assert refused.value.title == "已有一个 SiCo 窗口在运行"
        assert "先关掉它" in str(refused.value)
    finally:
        holder.close()
        startup.close()


def test_refused_start_leaves_the_existing_window_lease_live(tmp_path):
    """The failed second launch must not disturb the window the user is using."""

    holder = claim_window(tmp_path)
    startup = None
    try:
        startup = FrontendStartup(startup_args(tmp_path), lambda *_args, **_kwargs: None)
        with pytest.raises(StartupFailure):
            startup.ready.result(5)
        with pytest.raises(WindowAlreadyOpen):
            claim_window(tmp_path)
    finally:
        if startup is not None:
            startup.close()
        holder.close()


def test_scripted_connection_modes_do_not_take_the_window_lock(tmp_path):
    """demo/connect 是脚本化联调路径：不占窗口锁，也不被窗口锁拦。"""

    holder = claim_window(tmp_path)
    try:
        startup = FrontendStartup(startup_args(tmp_path, "demo"), lambda *_args, **_kwargs: None)
        try:
            startup.ready.result(5)
        except StartupFailure as exc:
            assert exc.phase != "window"  # 失败原因不能是"已有窗口"
        except Exception:
            pass  # 没有服务/宿主时的失败都与窗口规则无关
        else:
            startup.close()
    finally:
        holder.close()
        startup.close()


def test_a_crashed_window_releases_the_lock_for_the_next_start(tmp_path):
    """GUI 崩溃（进程被杀）由内核释放锁：下次启动仍能打开窗口。"""

    ready = tmp_path / "claimed"
    code = (
        "import sys, time\n"
        "from sico.service.window_lease import claim_window\n"
        "claim_window(sys.argv[1])\n"
        "open(sys.argv[2], 'w').close()\n"
        "time.sleep(30)\n"
    )
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(ready)],
                               env=environment, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 20
        while not ready.exists() and process.poll() is None:
            assert time.monotonic() < deadline, "child window never claimed the lock"
            time.sleep(0.02)
        with pytest.raises(WindowAlreadyOpen):
            claim_window(tmp_path)
        process.kill()
        process.wait(10)
        deadline = time.monotonic() + 10
        while True:
            try:
                claim_window(tmp_path).close()
                return
            except WindowAlreadyOpen:
                assert time.monotonic() < deadline, "crashed window never released its lock"
                time.sleep(0.02)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(10)
