from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_DISPATCHER_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "terminal"
    / "scripts"
    / "sico-ai-terminal-dispatcher.sh"
)
_PINYIN_SOURCE = _DISPATCHER_SOURCE.parents[2] / "bin" / "sico-ai-pinyin"


def _write_os_release(
    path: Path, identifier: str, version: str, *, identifier_like: str = ""
) -> None:
    lines = [f'ID="{identifier}"', f'VERSION_ID="{version}"']
    if identifier_like:
        lines.append(f'ID_LIKE="{identifier_like}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_terminal(path: Path, platform: str, exit_code: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/bash\n"
        f"printf 'target={platform}\\n'\n"
        "printf 'library-path=%s\\n' \"${LD_LIBRARY_PATH-unset}\"\n"
        "printf 'cad-temp=%s\\n' \"${CAD_TEMP_DIR-unset}\"\n"
        "printf 'sico-temp=%s\\n' \"${SICO_TEMP_DIR-unset}\"\n"
        "printf 'tmpdir=%s\\n' \"${TMPDIR-unset}\"\n"
        "printf 'tmp=%s\\n' \"${TMP-unset}\"\n"
        "printf 'temp=%s\\n' \"${TEMP-unset}\"\n"
        "printf 'sqlite-tmp=%s\\n' \"${SQLITE_TMPDIR-unset}\"\n"
        "printf 'xdg-cache=%s\\n' \"${XDG_CACHE_HOME-unset}\"\n"
        "printf 'xdg-runtime=%s\\n' \"${XDG_RUNTIME_DIR-unset}\"\n"
        "printf 'no-bytecode=%s\\n' \"${PYTHONDONTWRITEBYTECODE-unset}\"\n"
        "printf 'qterm-data=%s\\n' \"${SICO_AI_QTERMWIDGET_DATA_PATH-unset}\"\n"
        "if [[ -n ${SICO_AI_TEST_REPORT_PID:-} ]]; then\n"
        "    printf 'pid=%s\\n' \"$$\"\n"
        "fi\n"
        "if [[ -n ${SICO_AI_CONTROL_FD:-} ]]; then\n"
        "    IFS= read -r control <&\"$SICO_AI_CONTROL_FD\"\n"
        "    printf 'control=%s\\n' \"$control\"\n"
        "fi\n"
        "for argument in \"$@\"; do\n"
        "    printf 'argument=%s\\n' \"$argument\"\n"
        "done\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_dispatcher(
    tmp_path: Path,
    platforms: tuple[str, ...] = ("rhel7", "rhel8"),
) -> Path:
    from pinyin_wrapper_support import product_layout
    product_root = product_layout(tmp_path)
    install_root = product_root / "tools/ai"
    launcher = install_root / "bin" / "sico-ai-terminal"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(_DISPATCHER_SOURCE, launcher)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    pinyin = launcher.with_name("sico-ai-pinyin")
    shutil.copy2(_PINYIN_SOURCE, pinyin)
    pinyin.chmod(pinyin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    pyqt_launcher = install_root / "bin" / "sico-ai-terminal-pyqt"
    _write_terminal(pyqt_launcher, "launcher")
    (product_root / "share" / "qtermwidget5" / "kb-layouts").mkdir(parents=True)
    (product_root / "share" / "qtermwidget5" / "color-schemes").mkdir()
    (
        product_root / "share" / "qtermwidget5" / "kb-layouts" / "default.keytab"
    ).write_text("keymap\n", encoding="ascii")
    (
        product_root
        / "share"
        / "qtermwidget5"
        / "color-schemes"
        / "BreezeModified.colorscheme"
    ).write_text("scheme\n", encoding="ascii")
    for platform in platforms:
        runtime = install_root / "runtime" / platform
        (runtime / "python").mkdir(parents=True)
        (runtime / "lib").mkdir()
        (runtime / "python" / "QTermWidget.abi3.so").write_bytes(b"binding")
        (runtime / "lib" / "libqtermwidget5.so.1").write_bytes(b"library")
    return launcher


def _dispatcher_environment(**overrides: str) -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "SICO_HOME", "CAD_HOME", "SICO_ROOT", "CAD_AGENT_ROOT",
        "SICO_AI_PLATFORM",
        "SICO_AI_OS_RELEASE_FILE",
        "CAD_TEMP_DIR",
        "SICO_TEMP_DIR", "SICO_PYTHON", "SICO_PYTHON_ROOT",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SQLITE_TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "GSETTINGS_BACKEND",
        "PYTHONDONTWRITEBYTECODE",
        "SICO_PYTHON",
        "SICO_PYTHON_ROOT",
        "DBUS_SESSION_BUS_ADDRESS",
        "IBUS_ADDRESS",
        "IBUS_ADDRESS_FILE",
        "IBUS_USE_PORTAL",
        "QT_IM_MODULE",
        "XMODIFIERS",
        "LSB_JOBID",
    ):
        environment.pop(name, None)
    environment["SICO_PYTHON"] = "/bin/bash"
    environment.update(overrides)
    return {key: value for key, value in environment.items() if value is not None}


def _run_dispatcher(
    launcher: Path, *arguments: str, cwd: Path | None = None, **overrides: str
) -> subprocess.CompletedProcess[str]:
    overrides.setdefault("SICO_PYTHON", str(launcher.parents[3] / "bin/test-python"))
    return subprocess.run(
        [str(launcher), *arguments],
        check=False,
        text=True,
        capture_output=True,
        env=_dispatcher_environment(**overrides),
        cwd=launcher.parents[3].parent if cwd is None else cwd,
        timeout=5,
    )


@pytest.mark.parametrize(
    ("version", "platform"),
    (("7.9", "rhel7"), ("8.10", "rhel8"), ("9.8", "rhel8")),
)
def test_dispatcher_selects_rhel_runtime(tmp_path: Path, version: str, platform: str) -> None:
    launcher = _install_dispatcher(tmp_path)
    os_release = tmp_path / "os-release"
    _write_os_release(os_release, "rhel", version)

    result = _run_dispatcher(
        launcher,
        "argument with spaces",
        "--",
        "$(literal)",
        SICO_AI_OS_RELEASE_FILE=str(os_release),
        LD_LIBRARY_PATH="keep-this-value",
        PATH="/definitely-missing",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "target=launcher",
        "library-path=keep-this-value",
        "cad-temp=unset",
        f"sico-temp={tmp_path / '.sico'}",
        f"tmpdir={tmp_path / '.sico/ai'}",
        f"tmp={tmp_path / '.sico/ai'}",
        f"temp={tmp_path / '.sico/ai'}",
        f"sqlite-tmp={tmp_path / '.sico/ai'}",
        f"xdg-cache={tmp_path / '.sico/ai/cache'}",
        f"xdg-runtime={tmp_path / '.sico/ai/runtime'}",
        "no-bytecode=1",
        f"qterm-data={launcher.parents[3] / 'share' / 'qtermwidget5'}",
        "argument=argument with spaces",
        "argument=--",
        "argument=$(literal)",
    ]


def test_dispatcher_starts_launcher_with_configured_python(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    interpreter = tmp_path / "configured-python"
    log = tmp_path / "python-argv"
    from pinyin_wrapper_support import PYTHON_ADAPTER
    interpreter.write_text(
        PYTHON_ADAPTER + "printf '%s\\n' \"$1\" > \"$SICO_AI_TEST_PYTHON_LOG\"\n"
        "exec /bin/bash \"$@\"\n",
        encoding="ascii",
    )
    interpreter.chmod(0o755)

    result = _run_dispatcher(
        launcher,
        SICO_AI_PLATFORM="rhel7",
        SICO_PYTHON=str(interpreter),
        SICO_AI_TEST_PYTHON_LOG=str(log),
        PATH="/definitely-missing",
    )

    assert result.returncode == 0, result.stderr
    assert log.read_text(encoding="ascii").strip() == str(
        launcher.with_name("sico-ai-terminal-pyqt")
    )


def test_dispatcher_uses_sico_python_root(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    python_root = tmp_path / "python-root"
    interpreter = python_root / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(launcher.parents[3] / "bin/test-python")

    result = _run_dispatcher(
        launcher,
        SICO_AI_PLATFORM="rhel8",
        SICO_PYTHON=None,
        SICO_PYTHON_ROOT=str(python_root),
        PATH="/definitely-missing",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "target=launcher"


def test_dispatcher_requires_production_python(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)

    result = _run_dispatcher(
        launcher,
        SICO_AI_PLATFORM="rhel8",
        SICO_PYTHON="",
        SICO_PYTHON_ROOT="",
        PATH="/definitely-missing",
    )

    assert result.returncode != 0
    assert "SICO_PYTHON must not be empty" in result.stderr
    assert result.stdout == ""


def test_dispatcher_uses_process_launch_directory_for_cad_temp(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    launch = tmp_path / "launch path"
    launch.mkdir()

    result = _run_dispatcher(launcher, cwd=launch, SICO_AI_PLATFORM="rhel8")

    expected = str(launch / ".sico" / "ai")
    assert result.returncode == 0, result.stderr
    assert f"sico-temp={launch / '.sico'}" in result.stdout.splitlines()
    assert "cad-temp=unset" in result.stdout.splitlines()
    assert f"tmpdir={expected}" in result.stdout.splitlines()
    assert f"tmp={expected}" in result.stdout.splitlines()
    assert f"temp={expected}" in result.stdout.splitlines()
    assert f"sqlite-tmp={expected}" in result.stdout.splitlines()
    assert f"xdg-cache={expected}/cache" in result.stdout.splitlines()
    assert f"xdg-runtime={expected}/runtime" in result.stdout.splitlines()
    assert (launch / ".sico" / "ai").is_dir()
    assert (launch / ".sico" / "ai" / "runtime").stat().st_mode & 0o777 == 0o700
    assert not (launch / ".cad").exists()


def test_dispatcher_accepts_rhel_compatible_id_like(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    os_release = tmp_path / "os-release"
    _write_os_release(os_release, "custom-linux", "8.6", identifier_like="rhel fedora")

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(os_release))

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "target=launcher"


def test_dispatcher_platform_override_has_priority(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    os_release = tmp_path / "os-release"
    _write_os_release(os_release, "ubuntu", "24.04")

    result = _run_dispatcher(
        launcher,
        SICO_AI_PLATFORM="rhel7",
        SICO_AI_OS_RELEASE_FILE=str(os_release),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "target=launcher"


def test_dispatcher_rejects_unknown_program_name(tmp_path: Path) -> None:
    from pinyin_wrapper_support import product_layout
    product_root = product_layout(tmp_path)
    install_root = product_root / "tools/ai"
    launcher = install_root / "bin" / "cad-ai-unknown"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(_DISPATCHER_SOURCE, launcher)
    launcher.chmod(0o755)

    result = _run_dispatcher(launcher, SICO_AI_PLATFORM="rhel7")

    assert result.returncode != 0
    assert "unsupported program name" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("identifier", "version", "message"),
    (
        ("ubuntu", "24.04", "unsupported Linux family"),
        ("rhel", "10.0", "unsupported RHEL-family major version"),
        ("rhel", "8.", "invalid os-release VERSION_ID"),
        ("rhel", "8.preview", "invalid os-release VERSION_ID"),
    ),
)
def test_dispatcher_rejects_unsupported_systems(
    tmp_path: Path, identifier: str, version: str, message: str
) -> None:
    launcher = _install_dispatcher(tmp_path)
    os_release = tmp_path / "os-release"
    _write_os_release(os_release, identifier, version)

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(os_release))

    assert result.returncode != 0
    assert message in result.stderr
    assert result.stdout == ""


def test_dispatcher_does_not_evaluate_os_release_values(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    marker = tmp_path / "must-not-exist"
    os_release = tmp_path / "os-release"
    os_release.write_text(
        f'ID="rhel"\nVERSION_ID="$(touch {marker})"\n', encoding="utf-8"
    )

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(os_release))

    assert result.returncode != 0
    assert "invalid os-release VERSION_ID" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(
    ("content", "message"),
    (
        ('VERSION_ID="8.10"\n', "os-release is missing ID"),
        ('ID="rhel"\n', "os-release is missing VERSION_ID"),
    ),
)
def test_dispatcher_rejects_incomplete_os_release(
    tmp_path: Path, content: str, message: str
) -> None:
    launcher = _install_dispatcher(tmp_path)
    os_release = tmp_path / "os-release"
    os_release.write_text(content, encoding="utf-8")

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(os_release))

    assert result.returncode != 0
    assert message in result.stderr
    assert result.stdout == ""


def test_dispatcher_rejects_missing_os_release_override(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    missing = tmp_path / "missing-os-release"

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(missing))

    assert result.returncode != 0
    assert "SICO_AI_OS_RELEASE_FILE is not a readable regular file" in result.stderr
    assert result.stdout == ""


def test_dispatcher_rejects_os_release_fifo_without_blocking(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    fifo = tmp_path / "os-release.fifo"
    os.mkfifo(fifo)

    result = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(fifo))

    assert result.returncode != 0
    assert "SICO_AI_OS_RELEASE_FILE is not a readable regular file" in result.stderr
    assert result.stdout == ""


def test_dispatcher_rejects_invalid_override_and_missing_runtime(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path, ("rhel7",))
    os_release = tmp_path / "os-release"
    _write_os_release(os_release, "rhel", "8.10")

    invalid = _run_dispatcher(launcher, SICO_AI_PLATFORM="rhel9")
    missing = _run_dispatcher(launcher, SICO_AI_OS_RELEASE_FILE=str(os_release))

    assert invalid.returncode != 0
    assert "SICO_AI_PLATFORM must be rhel7 or rhel8" in invalid.stderr
    assert missing.returncode != 0
    assert "selected rhel8 QTermWidget binding is unavailable" in missing.stderr


def test_dispatcher_preserves_terminal_exit_status(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    terminal = launcher.with_name("sico-ai-terminal-pyqt")
    _write_terminal(terminal, "rhel7", exit_code=17)

    result = _run_dispatcher(launcher, SICO_AI_PLATFORM="rhel7")

    assert result.returncode == 17
    assert result.stdout.splitlines()[0] == "target=rhel7"


def test_dispatcher_relative_invocation_ignores_cdpath(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    relative_launcher = launcher.relative_to(tmp_path)
    result = subprocess.run(
        [str(relative_launcher)],
        cwd=tmp_path,
        check=False,
        text=True,
        capture_output=True,
        env=_dispatcher_environment(
            SICO_AI_PLATFORM="rhel8",
            SICO_PYTHON=str(launcher.parents[3] / "bin/test-python"),
            CDPATH=str(tmp_path),
            PATH="/definitely-missing",
        ),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "target=launcher"


def test_dispatcher_exec_preserves_pid_and_control_fd(tmp_path: Path) -> None:
    launcher = _install_dispatcher(tmp_path)
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"show\n")
    finally:
        os.close(write_fd)

    environment = _dispatcher_environment(
        SICO_AI_PLATFORM="rhel7",
        SICO_PYTHON=str(launcher.parents[3] / "bin/test-python"),
        SICO_AI_CONTROL_FD=str(read_fd),
        SICO_AI_TEST_REPORT_PID="1",
        PATH="/definitely-missing",
    )
    try:
        process = subprocess.Popen(
            [str(launcher)],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            pass_fds=(read_fd,),
        )
        stdout, stderr = process.communicate(timeout=5)
    finally:
        os.close(read_fd)

    assert process.returncode == 0, stderr
    assert f"pid={process.pid}" in stdout.splitlines()
    assert "control=show" in stdout.splitlines()
