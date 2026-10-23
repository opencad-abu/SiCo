"""Resolve and start one local CDNS-IC desktop; waiting and ownership stay elsewhere."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..installation import current as current_installation
from ..storage.journal import open_private
from ..storage.roots import agent_root

SOURCE_BOOTSTRAP = "scripts/sicoAutoLoad.il"
RESTORE_ENV = "SICO_VIRTUOSO_LAUNCH_RESTORE"
CIW_LAYOUT_ENV = "SICO_VIRTUOSO_LAUNCH_CIW_LAYOUT"
CIW_LAYOUT_LOWER_RIGHT = "lower-right-quarter"
# Python/Qt injections owned by the SiCo desktop must not leak into Virtuoso.
SANITIZED = ("PYTHONHOME", "PYTHONPATH", "LD_PRELOAD", "LD_AUDIT",
             "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_SELECT",
             "QT_DIR", "QML2_IMPORT_PATH")


class LaunchUnavailable(RuntimeError):
    """User-facing launch refusal; the message is presented verbatim."""


@dataclass(frozen=True)
class LaunchPlan:
    """One resolved launch: command, environment and private log location."""

    argv: tuple
    cwd: str
    environment: dict
    log_path: str
    bootstrap: str


def launch_plan(project, *, environment=None, root=None, which=shutil.which, moment=None):
    """Resolve the plan or refuse with an actionable message before any spawn."""
    environment = dict(os.environ if environment is None else environment)
    project = Path(project)
    if not project.is_absolute() or not project.is_dir():
        raise LaunchUnavailable("工程目录不可用，无法启动 CDNS-IC：" + str(project))
    if not environment.get("DISPLAY"):
        raise LaunchUnavailable(
            "当前会话没有可用的图形显示（DISPLAY），无法启动 CDNS-IC。\n"
            "请在有桌面显示的终端中启动 SiCo 后重试。")
    executable = which("virtuoso", path=environment.get("PATH"))
    if not executable:
        raise LaunchUnavailable(
            "未找到 virtuoso 可执行文件。\n"
            "请先在已加载站点 Cadence 环境的 shell 中启动 SiCo，再启动 CDNS-IC。")
    root = Path(root) if root is not None else current_installation(environment).root
    if not root.is_dir():
        raise LaunchUnavailable("SiCo 安装根不可用：" + str(root))
    bootstrap = _bootstrap(root, environment)
    argv = (str(executable),) + (("-restore", str(bootstrap)) if bootstrap else ())
    try:
        log_path = _log_path(project, moment)
    except ValueError as exc:
        raise LaunchUnavailable("工程状态目录不可写：" + str(exc)) from exc
    return LaunchPlan(argv, str(project), _launch_environment(root, environment),
                      str(log_path), str(bootstrap or ""))


def spawn_desktop(plan):
    """Start the detached desktop writing its private launch log; return the process."""
    fd = open_private(Path(plan.log_path), os.O_CREAT | os.O_WRONLY | os.O_APPEND)
    with os.fdopen(fd, "ab") as log:
        process = subprocess.Popen(
            list(plan.argv), cwd=plan.cwd, env=plan.environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    threading.Thread(target=process.wait, name="copilot-platform-reaper", daemon=True).start()
    return process


def _bootstrap(root, environment):
    """Prefer the source bootstrap; else an explicit site restore file."""
    source = root / SOURCE_BOOTSTRAP
    if source.is_file():
        return source
    configured = environment.get(RESTORE_ENV)
    if configured is None:
        return None
    path = Path(configured)
    if not (configured and path.is_absolute() and path.is_file()):
        raise LaunchUnavailable(
            RESTORE_ENV + " 必须指向存在的绝对路径文件；空值或相对路径不被接受。")
    return path


def _launch_environment(root, environment):
    launched = dict(environment)
    for name in SANITIZED:
        launched.pop(name, None)
    launched["SICO_HOME"] = str(root)
    launched[CIW_LAYOUT_ENV] = CIW_LAYOUT_LOWER_RIGHT
    return launched


def _log_path(project, moment):
    """Private runtime log location; only the launcher writes this component."""
    stamp = (moment or datetime.now()).strftime("%Y%m%dT%H%M%S")
    directory = agent_root(project, create=True) / "launch"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / ("cdns-ic-" + stamp + ".log")
