"""Observe real process references without commands, credentials, or signals in reports."""

import subprocess
import sys

from sico.migration.processes import scan_local


def test_process_with_legacy_open_descriptor_is_detected(tmp_path):
    source = tmp_path / ".cad"
    source.mkdir()
    path = source / "runtime"
    path.write_text("private")
    code = "import sys; f=open(sys.argv[1]); print('ready', flush=True); sys.stdin.read()"
    process = subprocess.Popen([sys.executable, "-c", code, str(path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        result = scan_local(source)
        assert process.pid in result["referencing_pids"]
        assert "private" not in str(result)
    finally:
        process.communicate(timeout=10)


def test_nas_alias_is_checked_against_physical_process_cwd(tmp_path):
    physical = tmp_path / "physical"
    source = physical / ".cad"
    source.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(physical)
    process = subprocess.Popen([sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
                               cwd=source, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        assert process.pid in scan_local(alias / ".cad")["referencing_pids"]
    finally:
        process.communicate(timeout=10)
