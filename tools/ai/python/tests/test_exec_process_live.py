"""Actual pinned exec-server tests; no model calls and no unsandboxed fallback."""

import json
import sys
import threading

import pytest

from exec_live_fixtures import drained, managed_configuration, managed_process, output, until


def test_same_live_shell_preserves_directory_export_function_and_unset(managed_process):
    with managed_process() as (process, work):
        process.write(b"printf 'first:%s\\n' \"$$\"; cd next; export SICO_FIXTURE=continued; "
                      b"unset EMPTY; sico_fixture() { printf function-ok; };\n")
        first = until(process, lambda rows: b"\n" in output(rows))
        pid = output(first).decode().strip().split(":")[1]
        process.write(b"printf 'second:%s:%s:%s:%s\\n' \"$$\" \"$PWD\" \"$SICO_FIXTURE\" "
                      b"\"${EMPTY-unset}\"; sico_fixture; exit 7\n")
        rest = drained(process)
        assert output(rest) == f"second:{pid}:{work / 'next'}:continued:unset\nfunction-ok".encode()
        assert next(row.exit_code for row in rest if row.kind == "exited") == 7
        assert not output(first + rest, "stderr")


def test_large_mixed_binary_output_is_not_truncated_or_duplicated(managed_process):
    expected = ("中文\x00\x1b[31m\n" * 50000).encode() + b"\xff\xfe"
    script = "import os; a=('中文\\0\\x1b[31m\\n'*50000).encode()+b'\\xff\\xfe'; " \
             "os.write(1,a); os.write(2,b'stderr\\0\\xff')"
    with managed_process([sys.executable, "-c", script]) as (process, _):
        rows = drained(process)
        assert output(rows) == expected
        assert output(rows, "stderr") == b"stderr\0\xff"
        assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
        assert rows[-2].kind == "exited" and rows[-2].exit_code == 0
        assert rows[-1].kind == "closed"


@pytest.mark.parametrize("access,allowed", [("read-only", False), ("workspace-write", True)])
def test_managed_filesystem_and_network_boundaries_remain_enforced(managed_process, tmp_path, access, allowed):
    outside = tmp_path / "outside"
    outside.mkdir()
    script = """
import json, pathlib, socket, sys
result = {}
for name, path in [('inside', pathlib.Path('allowed')), ('outside', pathlib.Path(sys.argv[1]) / 'denied')]:
    try:
        path.write_text('fixture')
        result[name] = True
    except OSError:
        result[name] = False
try:
    socket.socket().bind(('127.0.0.1', 0))
    result['network'] = True
except OSError:
    result['network'] = False
print(json.dumps(result))
"""
    with managed_process([sys.executable, "-c", script, str(outside)], access=access) as (process, work):
        rows = drained(process)
        assert rows[-2].exit_code == 0, output(rows, "stderr")
        assert json.loads(output(rows)) == dict(inside=allowed, outside=False, network=False)
        assert (work / "allowed").exists() == allowed
        assert not (outside / "denied").exists()


def test_explicit_environment_does_not_inherit_control_process_secrets(managed_process):
    command = [sys.executable, "-c", "import os,json; print(json.dumps(dict(os.environ)))"]
    with managed_process(command, environment={"PATH": "/bin", "EMPTY": "", "SITE": "中文"}) as (process, _):
        result = json.loads(output(drained(process)))
        assert result["EMPTY"] == "" and result["SITE"] == "中文"
        assert "SICO_API_KEY" not in result and "PYTHONPATH" not in result
        assert "CODEX_HOME" not in result


def test_explicit_network_scope_allows_local_socket_without_remote_access(managed_process):
    script = "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print('bound'); s.close()"
    with managed_process([sys.executable, "-c", script], network=True) as (process, _):
        rows = drained(process)
        assert output(rows) == b"bound\n" and rows[-2].exit_code == 0


def test_missing_launcher_path_fails_without_running_target(managed_process):
    with managed_process(["/bin/bash", "-c", "printf target-ran"], environment={"PATH": ""}) as (process, _):
        rows = drained(process)
        assert output(rows) == b""
        assert rows[-2].exit_code != 0


def test_concurrent_large_streams_have_no_missing_bytes_or_sequences(managed_process):
    script = """
import os, threading
def emit(fd, value):
    for _ in range(160):
        os.write(fd, value * 4096)
thread = threading.Thread(target=emit, args=(2, b'B'))
thread.start()
emit(1, b'A')
thread.join()
"""
    with managed_process([sys.executable, "-c", script]) as (process, _):
        rows = drained(process)
        assert output(rows) == b"A" * (4096 * 160)
        assert output(rows, "stderr") == b"B" * (4096 * 160)
        assert [row.sequence for row in rows] == list(range(1, len(rows) + 1))
        assert rows[-2].exit_code == 0 and rows[-1].kind == "closed"


def test_background_output_remains_a_process_stream_and_keeps_its_origin(managed_process):
    with managed_process() as (process, _):
        process.write(b"(sleep .1; printf background) & printf foreground; wait; exit\n")
        rows = drained(process)
        assert output(rows) == b"foregroundbackground"
        # No command completion or ownership is inferred from text in this layer.
        assert all(row.kind in ("output", "exited", "closed") for row in rows)


def test_terminate_is_observed_separately_and_close_fences_late_input(managed_process):
    with managed_process() as (process, _):
        process.write(b"printf ready; sleep 30\n")
        until(process, lambda rows: output(rows) == b"ready")
        process.terminate()
        rows = drained(process)
        assert any(row.kind == "exited" and row.exit_code != 0 for row in rows)
        process.close()
        with pytest.raises(RuntimeError):
            process.write(b"echo must-not-run\n")


def test_close_reaps_background_descendants(managed_process):
    with managed_process(access="workspace-write") as (process, work):
        process.write(b"(sleep 1; printf escaped > escaped) & printf ready\n")
        until(process, lambda rows: b"ready" in output(rows))
        process.close()
        # The supervisor's acknowledged cleanup, checked against a delayed side effect.
        threading.Event().wait(1.2)
        assert not (work / "escaped").exists()
