from __future__ import annotations

from pathlib import Path

from sico_ui import terminal_tool


def _runtime(root: Path, platform: str = "rhel8") -> Path:
    runtime = root / "runtime" / platform
    (runtime / terminal_tool.QTERMWIDGET_BINDING.parent).mkdir(parents=True)
    (runtime / terminal_tool.QTERMWIDGET_LIBRARY.parent).mkdir(parents=True)
    (runtime / terminal_tool.QTERMWIDGET_BINDING).write_bytes(b"binding")
    (runtime / terminal_tool.QTERMWIDGET_LIBRARY).write_bytes(b"library")
    return runtime


def test_platform_name_follows_os_release_and_override(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID="centos"\nVERSION_ID="7.9"\n', encoding="utf-8")
    assert terminal_tool.platform_name({}, release) == "rhel7"
    release.write_text('ID="rocky"\nVERSION_ID="8.10"\n', encoding="utf-8")
    assert terminal_tool.platform_name({}, release) == "rhel8"
    release.write_text('ID="rhel"\nVERSION_ID="9.8"\n', encoding="utf-8")
    assert terminal_tool.platform_name({}, release) == "rhel8"
    release.write_text('ID="fedora"\nVERSION_ID="40"\n', encoding="utf-8")
    assert terminal_tool.platform_name({}, release) is None
    assert terminal_tool.platform_name({"SICO_AI_PLATFORM": "rhel7"}, release) == "rhel7"
    assert terminal_tool.platform_name({"SICO_AI_PLATFORM": "suse"}, release) is None


def test_qtermwidget_runtime_uses_one_installation(tmp_path, monkeypatch):
    import pytest
    from sicopaths import Installation, IDENTITY_BYTES, MARKER
    (tmp_path / MARKER).parent.mkdir(parents=True)
    (tmp_path / MARKER).write_bytes(IDENTITY_BYTES)
    (tmp_path / "bin").mkdir()
    (tmp_path / "tools/common").mkdir(parents=True)
    runtime = _runtime(tmp_path / "tools/ai")
    monkeypatch.setattr(terminal_tool, "current", lambda env: Installation(tmp_path))
    environment = {"SICO_AI_PLATFORM": "rhel8"}
    assert terminal_tool.qtermwidget_runtime(environment) == runtime
    environment["SICO_AI_QTERMWIDGET_RUNTIME"] = str(tmp_path / "other")
    with pytest.raises(ValueError, match="conflicts"):
        terminal_tool.qtermwidget_runtime(environment)
    environment["SICO_AI_QTERMWIDGET_RUNTIME"] = ""
    with pytest.raises(ValueError):
        terminal_tool.qtermwidget_runtime(environment)
    environment["SICO_AI_QTERMWIDGET_RUNTIME"] = str(runtime)
    (runtime / terminal_tool.QTERMWIDGET_LIBRARY).unlink()
    assert terminal_tool.qtermwidget_runtime(environment) is None


def test_shell_command_wraps_the_login_shell_without_session_variables():
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/site",
        "SHELL": "/bin/bash",
        "LD_LIBRARY_PATH": "/software/pkgs/python/3.9.13/lib",
        "PYTHONPATH": "/runtime/python",
        "SICO_API_KEY": "sk-secret",
        "SICO_CODEX_API_KEY": "sk-secret",
        "SICO_AI_TOKEN": "session-secret",
        "SICO_AI_SOURCE_ROOT": "/software/cad/tools/ai",
    }

    command = terminal_tool.shell_command(environment)

    assert command[0] == "/usr/bin/env"
    dropped = [command[index + 1] for index, value in enumerate(command) if value == "-u"]
    assert dropped == sorted([
        "SICO_AI_TOKEN",
        "SICO_CODEX_API_KEY",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "SICO_API_KEY",
    ])
    # Configuration the user's own shell would carry is untouched.
    assert "SICO_AI_SOURCE_ROOT" not in dropped
    assert command[-2:] == ["/bin/bash", "-l"]


def test_shell_command_without_session_variables_is_a_plain_login_shell():
    assert terminal_tool.shell_command({"SHELL": "/bin/bash"}) == ["/bin/bash", "-l"]
    assert terminal_tool.shell_command({"SHELL": "/usr/bin/tcsh"}) == ["/usr/bin/tcsh"]
    assert terminal_tool.shell_command({}) == ["/bin/bash", "-l"]


def test_terminal_window_uses_the_shared_chrome_and_the_sico_scheme():
    source = Path(terminal_tool.__file__).read_text(encoding="utf-8")

    assert "class SiTerminalWindow(SiWidget)" in source
    assert "qtermwidget_type(0, self)" in source
    assert "apply_terminal_appearance(terminal, self._appearance)" in source
    assert 'launch_half = "right"' in source


def test_terminal_appearance_uses_body_text_size_and_the_theme_scheme(tmp_path, monkeypatch):
    from sico_ui.appearance import AppearancePreferences

    calls = []

    class Terminal:
        def setTerminalFont(self, font):
            calls.append(("font", font))

        def setColorScheme(self, scheme):
            calls.append(("scheme", Path(scheme).name))

    monkeypatch.setattr(
        terminal_tool,
        "fixed_pitch_terminal_font",
        lambda *, point_size=None: point_size,
    )
    terminal = Terminal()
    terminal_tool.apply_terminal_appearance(
        terminal,
        AppearancePreferences("dark", 16, 10),
        resource=lambda relative: tmp_path / relative,
    )

    assert calls == [("font", 16), ("scheme", "SiCoDark.colorscheme")]


def test_terminal_window_refreshes_appearance_in_place(tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QWidget
    from sico_ui.appearance import AppearancePreferences

    calls = []

    class Terminal(QWidget):
        ScrollBarRight = 1

        def __init__(self, start, parent):
            super().__init__(parent)
            assert start == 0

        def setShellProgram(self, program):
            pass

        def setArgs(self, arguments):
            pass

        def setEnvironment(self, entries):
            pass

        def setWorkingDirectory(self, directory):
            pass

        def setHistorySize(self, size):
            pass

        def setScrollBarPosition(self, position):
            pass

        def setTextCodec(self, codec):
            pass

        def setTerminalFont(self, font):
            calls.append(("font", font))

        def setColorScheme(self, scheme):
            calls.append(("scheme", Path(scheme).name))

        def startShellProgram(self):
            pass

    monkeypatch.setattr(
        terminal_tool,
        "fixed_pitch_terminal_font",
        lambda *, point_size=None: point_size,
    )
    monkeypatch.setattr(
        terminal_tool, "terminal_resource", lambda relative: tmp_path / relative)
    monkeypatch.setattr(
        terminal_tool.SiTerminalWindow, "_install_context_menu", lambda *_: None)

    window = terminal_tool.SiTerminalWindow(
        Terminal,
        workspace=tmp_path,
        environment={"SHELL": "/bin/bash"},
        appearance=AppearancePreferences("light", 10, 10),
    )
    try:
        assert calls[-2:] == [("font", 10), ("scheme", "SiCo.colorscheme")]
        window.refresh_appearance(AppearancePreferences("dark", 15, 10))
        assert calls[-2:] == [("font", 15), ("scheme", "SiCoDark.colorscheme")]
    finally:
        window.close()


def test_open_terminal_reports_when_no_runtime_is_available(monkeypatch):
    monkeypatch.setattr(terminal_tool, "qtermwidget_runtime", lambda *args, **kwargs: None)
    reported = []
    monkeypatch.setattr(
        terminal_tool,
        "report_unavailable",
        lambda window, message: reported.append(message),
    )

    terminal_tool.open_terminal(None)

    assert reported and "QTermWidget" in reported[0]


def test_launch_directory_follows_the_session_journal(tmp_path):
    project = tmp_path / "project"
    root = project / ".cad" / "ai" / "agent"
    root.mkdir(parents=True)
    window = type("Window", (), {})()
    window.api = type("Frontend", (), {"launch_directory": project})()

    assert terminal_tool.launch_directory(window) == project


def test_environment_exports_share_the_service_owner():
    from sico.service import terminal_environment

    for name in ("LOGIN_SHELLS", "SHELL_ENV_DROP", "shell_command"):
        assert getattr(terminal_tool, name) is getattr(terminal_environment, name)


def test_terminal_installs_environment_before_starting_the_shell(tmp_path, monkeypatch):
    from PyQt5.QtWidgets import QWidget
    from cadenv import preserve_virtuoso_environment

    calls = []
    monkeypatch.setenv("SICO_AI_TOKEN", "synthetic-gui-token")

    class Terminal(QWidget):
        ScrollBarRight = 1

        def __init__(self, start, parent):
            super().__init__(parent)
            assert start == 0

        def setShellProgram(self, program):
            calls.append(("program", program))

        def setArgs(self, arguments):
            calls.append(("args", arguments))

        def setEnvironment(self, entries):
            calls.append(("environment", dict(entry.split("=", 1) for entry in entries)))

        def setWorkingDirectory(self, directory):
            calls.append(("cwd", directory))

        def setHistorySize(self, size):
            pass

        def setScrollBarPosition(self, position):
            pass

        def setTextCodec(self, codec):
            pass

        def setTerminalFont(self, font):
            pass

        def startShellProgram(self):
            assert [item[0] for item in calls] == ["program", "args", "environment", "cwd"]
            calls.append(("started", True))

    monkeypatch.setattr(terminal_tool, "terminal_resource", lambda _: None)
    monkeypatch.setattr(terminal_tool.SiTerminalWindow, "_install_context_menu", lambda *_: None)
    source = {"PATH": "/site/bin:/bin", "PYTHONPATH": "", "SITE_VALUE": "a=b\n中文"}
    preserve_virtuoso_environment(source)
    source.update(PYTHONPATH="/sico/python", LD_LIBRARY_PATH="/sico/lib", SICO_AI_TOKEN="secret")
    original = dict(source)
    window = terminal_tool.SiTerminalWindow(Terminal, workspace=tmp_path, environment=source)
    assert source == original
    assert calls[0] == ("program", "/usr/bin/env")
    assert "secret" not in calls[1][1]
    assert "synthetic-gui-token" not in calls[1][1]
    assert "SICO_AI_TOKEN" in calls[1][1]
    assert calls[2][1] == {"PATH": "/site/bin:/bin", "PYTHONPATH": "", "SITE_VALUE": "a=b\n中文"}
    assert calls[-1] == ("started", True)
    window.close()


def test_invalid_environment_is_reported_without_constructing_a_terminal(monkeypatch):
    reported, constructed = [], []
    monkeypatch.setattr(terminal_tool, "qtermwidget_runtime", lambda: Path("/fixture/runtime"))
    monkeypatch.setattr(terminal_tool, "load_qtermwidget", lambda _: lambda *_: constructed.append(True))
    monkeypatch.setattr(terminal_tool, "report_unavailable", lambda _, message: reported.append(message))
    monkeypatch.setenv("SICO_VIRTUOSO_PATH", "/fixture/bin")
    monkeypatch.setenv("SICO_VIRTUOSO_PATH_SET", "0")
    terminal_tool.open_terminal(None)
    assert not constructed
    assert reported and "无法准备终端环境" in reported[0]
