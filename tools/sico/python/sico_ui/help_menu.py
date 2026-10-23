"""Far-right help menu: offline user manual and product version information."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

from PyQt5.QtCore import QT_VERSION_STR
from PyQt5.QtWidgets import QMenu, QToolButton
from sicoresources import current, manual_path

import sico

BROWSER_NAMES = ("firefox", "firefox-esr")
BROWSER_ENV_DROP = (
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "LD_AUDIT",
    "PYTHONHOME",
    "PYTHONPATH",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def version_text():
    """Product and runtime versions without model/session details."""
    try:
        root = current().root
    except (OSError, ValueError):
        root = "未知"
    return "\n".join((
        "产品：" + sico.PRODUCT_NAME,
        "版本：" + sico.__version__,
        "Python：" + platform.python_version(),
        "Qt：" + QT_VERSION_STR,
        "安装目录：" + str(root),
    ))


def show_user_manual(window):
    """Open the reviewed HTML manual in Firefox, bypassing MIME associations."""
    path = manual_path()
    if path is None:
        window.info_app.notice("用户手册暂不可用", "当前安装未包含 docs/html 用户手册。")
        return False
    if not open_manual(path):
        window.info_app.notice(
            "用户手册未打开",
            "未找到 Firefox，或浏览器启动失败。请确认 firefox 可用后重试：" + str(path),
        )
        return False
    return True


def browser_path():
    """Resolve Firefox explicitly; never use the desktop HTML association."""
    for name in BROWSER_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


def browser_environment():
    """Do not leak EDA/Python/Qt search paths into the browser process."""
    environment = dict(os.environ)
    for name in BROWSER_ENV_DROP:
        environment.pop(name, None)
    return environment


def open_manual(path):
    """Launch one detached Firefox window for a local HTML file."""
    browser = browser_path()
    if browser is None:
        return False
    try:
        subprocess.Popen(
            [browser, "--new-window", path.resolve().as_uri()],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=browser_environment(),
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        return False
    return True


def show_version_info(window):
    """Show product/runtime versions through the shared information window."""
    window.info_app.info("版本信息", version_text())


def install_help_button(menu_bar, window):
    """Place the 帮助 button at the right edge of one main window's menu bar."""
    menu = QMenu("帮助", window)
    menu.addAction("用户手册", lambda: show_user_manual(window))
    menu.addAction("版本信息", lambda: show_version_info(window))
    button = QToolButton(menu_bar)
    button.setObjectName("helpButton")
    button.setText("帮助")
    button.setToolTip("用户手册与版本信息")
    button.setAccessibleName("帮助")
    button.setPopupMode(QToolButton.InstantPopup)
    button.setMenu(menu)
    menu_bar.setCornerWidget(button)
    return button, menu
