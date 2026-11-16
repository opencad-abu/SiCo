"""Standalone composer IPC, lifecycle, and real IBus pinyin on an owned display."""

from __future__ import annotations

import base64
import json
import os
import select
import shutil
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from agent_gui_support import isolated_display, quick_form, screenshot, wait_until, xdo

CAD = Path(__file__).absolute().parents[3]


def line(process):
    assert select.select([process.stdout], [], [], 12)[0], "Composer did not submit"
    return process.stdout.readline().decode("ascii")


def send(process, **message):
    process.stdin.write((json.dumps(message) + "\n").encode())
    process.stdin.flush()


@contextmanager
def composer(directory, env):
    with (directory / "composer.log").open("w") as log:
        process = subprocess.Popen(
            [
                env["CAD_PYTHON"],
                "-s",
                str(CAD / "sico/python/sico-entry"),
                "quick-input",
                "--launch-dir",
                str(directory),
            ],
            env=env,
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        try:
            send(process, kind="open", source="中文测试库 / 输入", destination="Session: test")
            yield process
        finally:
            if process.poll() is None:
                if not process.stdin.closed:
                    process.stdin.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdout.close()


def test_composer_ipc_rejection_and_parent_eof_preserve_utf8_draft(tmp_path):
    with isolated_display(tmp_path) as env, composer(tmp_path, env) as process:
        text = '中文快速输入\n引号"反斜线\\'
        quick_form(env, text)
        encoded = line(process)
        assert encoded.startswith("@text ")
        assert base64.b64decode(encoded.split()[1]).decode() == text
        assert not (tmp_path / ".cad/ai/agent/sessions").exists()
        send(process, kind="rejected", message="请重试；文字保留。")
        # Show is ordered after rejection; submit through the actual Enter key.
        quick_form(env, submit=False)
        xdo(env, "sleep", "0.2", "key", "Return")
        assert line(process) == encoded
        process.stdin.close()
        assert process.wait(timeout=5) == 0
        drafts = list((tmp_path / ".cad/ai/agent/runtime").glob("quick-*/draft.txt"))
        assert len(drafts) == 1 and drafts[0].read_text() == text


def test_composer_sigterm_preserves_unsent_draft(tmp_path):
    with isolated_display(tmp_path) as env:
        env["SICO_PYTHON"] = env["CAD_PYTHON"]
        with composer(tmp_path, env) as process:
            text = '父进程退出前保留\n"引号"和反斜线\\'
            quick_form(env, text, submit=False)
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=5) == 0
            drafts = list((tmp_path / ".sico/ai/agent/runtime").glob("quick-*/draft.txt"))
            assert len(drafts) == 1 and drafts[0].read_text() == text


@pytest.mark.skipif(
    os.environ.get("SICO_IME_TESTS") != "1"
    or not all(shutil.which(name) for name in ("ibus", "ibus-daemon", "dbus-daemon"))
    or not Path("/usr/share/ibus/component/libpinyin.xml").exists(),
    reason="Opt-in real isolated IBus/libpinyin test",
)
def test_real_pinyin_preedit_commit_and_ipc(tmp_path):
    processes = []
    with isolated_display(tmp_path) as env, (tmp_path / "ibus.log").open("w") as log:
        env = {k: v for k, v in env.items() if not k.startswith(("DBUS_", "IBUS_"))}
        for name in ("config", "cache", "runtime", "socket"):
            (tmp_path / name).mkdir(mode=0o700)
        env.update(
            XDG_CONFIG_HOME=str(tmp_path / "config"),
            XDG_CACHE_HOME=str(tmp_path / "cache"),
            XDG_RUNTIME_DIR=str(tmp_path / "runtime"),
            GSETTINGS_BACKEND="keyfile",
            IBUS_ADDRESS_FILE=str(tmp_path / "ibus.address"),
            QT_IM_MODULE="ibus",
            XMODIFIERS="@im=ibus",
            LANG="en_US.UTF-8",
            GIO_USE_VFS="local",
            NO_AT_BRIDGE="1",
        )
        try:
            bus = subprocess.Popen(
                ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                env=env,
                cwd=tmp_path,
                stdout=subprocess.PIPE,
                stderr=log,
                start_new_session=True,
            )
            processes.append(bus)
            assert select.select([bus.stdout], [], [], 5)[0]
            env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().decode().strip()
            bus.stdout.close()
            subprocess.run(
                [
                    "gsettings",
                    "set",
                    "org.freedesktop.ibus.general",
                    "preload-engines",
                    "['libpinyin']",
                ],
                env=env,
                check=True,
                timeout=5,
                stdout=log,
                stderr=log,
            )
            daemon = subprocess.Popen(
                [
                    "ibus-daemon",
                    "--address=unix:tmpdir=/tmp",
                    "--cache=none",
                    "--panel=disable",
                    "--emoji-extension=disable",
                ],
                env=env,
                cwd=tmp_path,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            processes.append(daemon)
            wait_until(lambda: (tmp_path / "ibus.address").exists(), 10)
            env["IBUS_ADDRESS"] = next(
                item.split("=", 1)[1]
                for item in (tmp_path / "ibus.address").read_text().splitlines()
                if item.startswith("IBUS_ADDRESS=")
            )
            # Use the private IBus address directly, as the Qt plugin does;
            # the generic desktop portal does not expose engine selection.
            env.pop("DBUS_SESSION_BUS_ADDRESS")
            with composer(tmp_path, env) as process:
                quick_form(env, submit=False)

                def engine_ready():
                    selected = subprocess.run(
                        ["ibus", "engine", "libpinyin"],
                        env=env,
                        timeout=5,
                        capture_output=True,
                        text=True,
                    )
                    current = subprocess.run(
                        ["ibus", "engine"], env=env, timeout=5, capture_output=True, text=True
                    )
                    (tmp_path / "engine.json").write_text(
                        json.dumps(
                            {
                                "select_code": selected.returncode,
                                "select_out": selected.stdout,
                                "select_err": selected.stderr,
                                "current": current.stdout,
                                "current_err": current.stderr,
                            },
                            indent=2,
                        )
                    )
                    return current.stdout.strip() == "libpinyin"

                wait_until(engine_ready, 10)
                xdo(env, "type", "--clearmodifiers", "zhongwen")
                screenshot(env, tmp_path / "pinyin-preedit.png")
                xdo(env, "key", "space")
                xdo(env, "type", "--clearmodifiers", "shuru")
                xdo(env, "key", "space")
                screenshot(env, tmp_path / "pinyin-committed.png")
                quick_form(env)
                submitted = base64.b64decode(line(process).split()[1]).decode()
                assert submitted == "中文输入", repr(submitted)
                send(process, kind="accepted")
                assert process.wait(timeout=5) == 0
        finally:
            for process in reversed(processes):
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)
