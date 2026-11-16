from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import cadai.runtime as runtime_module
from cadai.runtime import RuntimePaths, read_spool, write_spool


def test_runtime_and_spool_permissions_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    runtime = RuntimePaths.create()
    assert runtime.root.parent == tmp_path / ".sico" / "ai"
    assert runtime.root.name.startswith("sico-ai-")
    assert not (tmp_path / ".cad").exists()
    assert runtime.root.stat().st_mode & 0o777 == 0o700
    metadata = write_spool(runtime.spool, "source", "电源".encode())
    payload = runtime.spool / str(metadata["name"])
    assert payload.stat().st_mode & 0o777 == 0o600
    assert read_spool(runtime.spool, str(metadata["name"])) == "电源".encode()
    skill = write_spool(runtime.spool, "load", b"nil\n", suffix=".ils")
    assert str(skill["name"]).endswith(".ils")
    runtime.cleanup()
    assert not runtime.root.exists()


def test_runtime_honors_frontend_temp_root_after_cwd_change(
    tmp_path: Path, monkeypatch
) -> None:
    launch_temp = tmp_path / "launch" / ".sico"
    launch_temp.mkdir(parents=True, mode=0o700)
    changed = tmp_path / "changed"
    changed.mkdir()
    monkeypatch.chdir(changed)
    monkeypatch.setenv("SICO_TEMP_DIR", str(launch_temp))

    runtime = RuntimePaths.create()

    assert runtime.root.parent == launch_temp / "ai"
    runtime.cleanup()


def test_split_runtime_keeps_mcp_local_and_cleans_shared_bridge(
    tmp_path: Path, monkeypatch
) -> None:
    local_base = tmp_path / "node-local-runtime"
    shared_base = tmp_path / "shared-workspace"
    shared_temp = shared_base / ".sico"
    local_base.mkdir(mode=0o700)
    shared_temp.mkdir(parents=True, mode=0o700)
    subprocess.run(["git", "init", "-q", str(shared_base)], check=True)
    local_sentinel = local_base / "keep-local"
    shared_sentinel = shared_base / "keep-shared"
    local_sentinel.write_text("local\n", encoding="ascii")
    shared_sentinel.write_text("shared\n", encoding="ascii")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(local_base))

    runtime = RuntimePaths.create(spool_base=shared_temp, runtime_base=local_base)

    assert runtime.root.parent == local_base
    assert runtime.socket == runtime.root / "bridge.sock"
    assert runtime.spool == runtime.root / "spool"
    assert runtime.bridge_spool is not None
    assert runtime.virtuoso_spool == runtime.bridge_spool
    assert runtime.bridge_spool.parent == (
        shared_temp / f".sico-ai-runtime-{os.getuid()}"
    )
    assert runtime.bridge_spool.name.startswith("session-")
    assert runtime.root.stat().st_mode & 0o777 == 0o700
    assert runtime.spool.stat().st_mode & 0o777 == 0o700
    assert runtime.bridge_spool.stat().st_mode & 0o777 == 0o700
    ignore = runtime.bridge_spool.parent / ".gitignore"
    assert ignore.read_bytes() == b"*\n"
    assert ignore.stat().st_mode & 0o777 == 0o600
    ignored_probe = runtime.bridge_spool / "result-probe.json"
    ignored_probe.write_text("{}\n", encoding="ascii")
    for path in (ignore, ignored_probe):
        subprocess.run(
            [
                "git",
                "-C",
                str(shared_base),
                "check-ignore",
                "--quiet",
                "--",
                str(path.relative_to(shared_base)),
            ],
            check=True,
        )
    ignored_probe.unlink()
    mcp_runtime = RuntimePaths.from_root(runtime.root, runtime.spool)
    assert mcp_runtime == RuntimePaths(runtime.root, runtime.socket, runtime.spool)
    assert mcp_runtime.virtuoso_spool == runtime.spool

    runtime_root = runtime.root
    bridge_spool = runtime.bridge_spool
    bridge_container = bridge_spool.parent
    runtime.cleanup()

    assert not runtime_root.exists()
    assert not bridge_spool.exists()
    assert bridge_container.is_dir()
    assert ignore.read_bytes() == b"*\n"
    assert local_sentinel.read_text(encoding="ascii") == "local\n"
    assert shared_sentinel.read_text(encoding="ascii") == "shared\n"


def test_bridge_ignore_publish_failure_is_recoverable(
    tmp_path: Path, monkeypatch
) -> None:
    local_base = tmp_path / "node-local-runtime"
    shared_base = tmp_path / "shared-workspace"
    shared_temp = shared_base / ".sico"
    local_base.mkdir(mode=0o700)
    shared_temp.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(local_base))
    real_fsync = runtime_module.os.fsync

    def fail_fsync(_fd: int) -> None:
        raise OSError("shared filesystem write failed")

    monkeypatch.setattr(runtime_module.os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="shared filesystem write failed"):
        RuntimePaths.create(spool_base=shared_temp, runtime_base=local_base)

    container = shared_temp / f".sico-ai-runtime-{os.getuid()}"
    assert container.is_dir()
    assert list(container.iterdir()) == []
    assert list(local_base.iterdir()) == []

    monkeypatch.setattr(runtime_module.os, "fsync", real_fsync)
    runtime = RuntimePaths.create(spool_base=shared_temp, runtime_base=local_base)
    assert (container / ".gitignore").read_bytes() == b"*\n"
    runtime.cleanup()


def test_runtime_creation_cleans_root_when_permission_setup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    local_base = tmp_path / "node-local-runtime"
    local_base.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(local_base))

    def fail_chmod(_path: Path, _mode: int) -> None:
        raise OSError("permission setup failed")

    monkeypatch.setattr(runtime_module.os, "chmod", fail_chmod)
    with pytest.raises(OSError, match="permission setup failed"):
        RuntimePaths.create(runtime_base=local_base)

    assert list(local_base.iterdir()) == []


def test_cleanup_removes_local_root_when_bridge_spool_is_already_gone(
    tmp_path: Path, monkeypatch
) -> None:
    local_base = tmp_path / "node-local-runtime"
    shared_base = tmp_path / "shared-workspace"
    shared_temp = shared_base / ".sico"
    local_base.mkdir(mode=0o700)
    shared_temp.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(local_base))
    runtime = RuntimePaths.create(spool_base=shared_temp, runtime_base=local_base)
    assert runtime.bridge_spool is not None
    runtime.bridge_spool.rmdir()

    runtime.cleanup()

    assert not runtime.root.exists()


def test_cleanup_removes_bridge_spool_when_local_spool_is_already_gone(
    tmp_path: Path, monkeypatch
) -> None:
    local_base = tmp_path / "node-local-runtime"
    shared_base = tmp_path / "shared-workspace"
    shared_temp = shared_base / ".sico"
    local_base.mkdir(mode=0o700)
    shared_temp.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(local_base))
    runtime = RuntimePaths.create(spool_base=shared_temp, runtime_base=local_base)
    assert runtime.bridge_spool is not None
    bridge_spool = runtime.bridge_spool
    runtime.spool.rmdir()

    runtime.cleanup()

    assert not runtime.root.exists()
    assert not bridge_spool.exists()


def test_spool_basename_and_symlink_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "cad-ai-test"
    spool = root / "spool"
    spool.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(spool, 0o700)
    target = spool / "target"
    target.write_text("payload", encoding="utf-8")
    (spool / "link").symlink_to(target)
    with pytest.raises(ValueError, match="basename"):
        read_spool(spool, "../target")
    with pytest.raises(ValueError, match="regular"):
        read_spool(spool, "link")
