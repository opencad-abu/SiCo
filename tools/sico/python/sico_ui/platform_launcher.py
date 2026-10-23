"""Launch one local CDNS-IC desktop and wait for its bridge registration."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from sico.service.platform_launch import launch_plan, spawn_desktop
from .receipts import DataReceipt


class PlatformLauncher(QObject):
    """取基线、拉起进程、轮询注册；回调都在 Qt 线程，取消不结束桌面进程。"""

    registered = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, api, project, *, parent=None, root=None, environment=None,
                 timeout=45.0, interval=1000, plan_source=None, spawn=None):
        super().__init__(parent)
        self.api = api
        self.project = Path(project)
        self.root, self.environment = root, environment
        self.timeout, self.interval = timeout, interval
        self._plan_source = plan_source or launch_plan
        self._spawn = spawn or spawn_desktop
        self.active = False
        self.plan = None
        self.process = None
        self._deadline = None
        self._baseline = None
        self._receipt = DataReceipt(self)
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._tick)

    def start(self):
        """Resolve the plan（预检失败抛出 ``LaunchUnavailable``）后异步等待注册。"""
        if self.active:
            return
        self.plan = self._plan_source(self.project, root=self.root,
                                      environment=self.environment)
        self.active = True
        self._deadline = time.monotonic() + self.timeout
        self._receipt.watch(self.api.platforms.instances(), self._baseline_ready,
                            self._baseline_failed)
        self._timer.start()

    def cancel(self):
        """停止等待；已经拉起的桌面保持运行。"""
        self._finish()

    def _baseline_ready(self, rows):
        if not self.active:
            return
        self._baseline = {row["id"] for row in rows}
        try:
            self.process = self._spawn(self.plan)
        except Exception as exc:
            self._fail("启动 CDNS-IC 进程失败：" + str(exc))

    def _baseline_failed(self, error):
        if self.active:
            self._fail("无法确认启动前的实例列表，未启动 CDNS-IC。" + str(error))

    def _tick(self):
        if not self.active or self._receipt.pending is not None:
            return
        if self.process is not None and self.process.poll() is not None:
            self._fail("CDNS-IC 进程提前退出（返回码 " + str(self.process.returncode) +
                       "）。\n日志：" + self.plan.log_path)
            return
        if time.monotonic() >= self._deadline:
            self._fail("等待 CDNS-IC 实例注册超时。\n请检查 Virtuoso 是否已打开并加载站点 "
                       "SiCo 集成。\n日志：" + self.plan.log_path)
            return
        self._receipt.watch(self.api.platforms.instances(), self._observed, self._query_failed)

    def _observed(self, rows):
        if not self.active:
            return
        for row in rows:
            if row["id"] in self._baseline or not _same_project(row.get("project"), self.project):
                continue
            candidate = row["id"]
            self._finish()
            self.registered.emit(candidate)
            return

    def _query_failed(self, _error):
        """查询失败不终止等待：下一次轮询重试，直到超时或进程退出。"""

    def _fail(self, message):
        self._finish()
        self.failed.emit(message)

    def _finish(self):
        self.active = False
        self._timer.stop()
        self._receipt.clear()


def _same_project(value, project):
    if not value:
        return False
    return os.path.normpath(str(value)) == os.path.normpath(str(project))
