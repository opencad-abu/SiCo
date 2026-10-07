"""Creation and validation of private per-session runtime files."""

from __future__ import annotations

import errno
import hashlib
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sicotemp import ai_directory, state_path

MAX_EVAL_BYTES = 65_536
MAX_SPOOL_BYTES = 8 * 1024 * 1024
BRIDGE_CONTAINER_PREFIX = ".cad-ai-runtime-"
BRIDGE_SPOOL_PREFIX = "session-"
_UNIX_SOCKET_PATH_BYTES = 107


@contextmanager
def unix_socket_address(path: Path) -> Iterator[str]:
    """Yield a short address while keeping the socket at its requested path."""
    rendered = str(path)
    if len(os.fsencode(rendered)) <= _UNIX_SOCKET_PATH_BYTES:
        yield rendered
        return

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path.parent, flags)
    try:
        shortened = f"/proc/self/fd/{descriptor}/{path.name}"
        if len(os.fsencode(shortened)) > _UNIX_SOCKET_PATH_BYTES:
            raise OSError(f"Unix socket filename is too long: {path.name}")
        yield shortened
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    socket: Path
    spool: Path
    bridge_spool: Path | None = None

    @property
    def virtuoso_spool(self) -> Path:
        return self.bridge_spool or self.spool

    @classmethod
    def create(
        cls,
        spool_base: str | Path | None = None,
        runtime_base: str | Path | None = None,
    ) -> RuntimePaths:
        base = _runtime_base(runtime_base)
        root = Path(tempfile.mkdtemp(prefix=_runtime_prefix(base), dir=base))
        bridge_spool: Path | None = None
        try:
            os.chmod(root, 0o700)
            spool = root / "spool"
            spool.mkdir(mode=0o700)
            if spool_base is not None:
                shared_base = Path(spool_base).expanduser().absolute()
                if not shared_base.is_dir():
                    raise NotADirectoryError(shared_base)
                if shared_base.name not in {".cad", ".sico"} and not (
                    shared_base.name == "ai" and shared_base.parent.name in {".cad", ".sico"}
                ):
                    raise ValueError("spool_base must name a project state directory")
                state = state_path(shared_base, legacy=True)
                ai_directory({}, temporary=state, create=True)
                container = _bridge_container(shared_base)
                bridge_spool = Path(tempfile.mkdtemp(prefix=BRIDGE_SPOOL_PREFIX, dir=container))
                os.chmod(bridge_spool, 0o700)
            return cls(
                root=root,
                socket=root / "bridge.sock",
                spool=spool,
                bridge_spool=bridge_spool,
            )
        except BaseException:
            if bridge_spool is not None:
                try:
                    bridge_spool.rmdir()
                except OSError:
                    pass
            shutil.rmtree(root, ignore_errors=True)
            raise

    @classmethod
    def from_root(cls, root: str | Path, spool: str | Path | None = None) -> RuntimePaths:
        path = Path(root).absolute()
        _require_private_directory(path)
        spool_path = path / "spool" if spool is None else Path(spool).absolute()
        _require_private_directory(spool_path)
        return cls(root=path, socket=path / "bridge.sock", spool=spool_path)

    def cleanup(self) -> None:
        _require_private_directory(self.root)
        if not (self.root.name.startswith("cad-ai-") or self.root.name.startswith("sico-ai-")):
            raise RuntimeError(f"refusing to remove unexpected runtime path: {self.root}")
        if self.spool.parent != self.root or self.spool.name != "spool":
            raise RuntimeError(f"refusing to remove unexpected local spool path: {self.spool}")
        try:
            try:
                _require_private_directory(self.spool)
            except FileNotFoundError:
                pass
            self._cleanup_bridge_spool()
        finally:
            shutil.rmtree(self.root)

    def _cleanup_bridge_spool(self) -> None:
        if self.bridge_spool is None:
            return
        bridge = self.bridge_spool
        container = bridge.parent
        if container.name != _bridge_container_name(container.parent) or not bridge.name.startswith(
            BRIDGE_SPOOL_PREFIX
        ):
            raise RuntimeError(f"refusing to remove unexpected bridge spool path: {bridge}")
        _require_private_directory(container)
        try:
            _require_private_directory(bridge)
        except FileNotFoundError:
            return
        bridge.rmdir()


def _bridge_container_name(base: Path) -> str:
    state = state_path(base, legacy=True)
    prefix = ".sico-ai-runtime-" if state.name == ".sico" else BRIDGE_CONTAINER_PREFIX
    return f"{prefix}{os.getuid()}"


def _bridge_container(shared_base: Path) -> Path:
    shared_base.mkdir(parents=True, mode=0o700, exist_ok=True)
    container = shared_base / _bridge_container_name(shared_base)
    try:
        container.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _require_private_directory(container)
    _ensure_private_gitignore(container)
    return container


def _ensure_private_gitignore(container: Path) -> None:
    path = container / ".gitignore"
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".gitignore-", dir=container)
    temporary = Path(temporary_name)
    try:
        os.fchmod(temporary_fd, 0o600)
        with os.fdopen(temporary_fd, "wb") as handle:
            temporary_fd = -1
            handle.write(b"*\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            pass
        _require_private_gitignore(path)
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        temporary.unlink(missing_ok=True)


def _require_private_gitignore(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(f"bridge ignore file is not private or valid: {path}")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            payload = handle.read(3)
    except OSError as exc:
        raise RuntimeError(f"bridge ignore file is not private or valid: {path}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if payload != b"*\n" or info.st_size != len(payload):
        raise RuntimeError(f"bridge ignore file is not private or valid: {path}")


def ai_temp_root(value: str | Path) -> Path:
    """Use a project's private state root, preserving the site's mount spelling.

    Accept either ``.sico[/ai]`` or legacy ``.cad[/ai]`` and normalize once.
    realpath() can
    turn a shared mount alias into a host-only path that other nodes cannot read.
    """
    return state_path(value, legacy=True) / "ai"


def _runtime_base(base: str | Path | None = None) -> str:
    path = ai_directory(create=True) if base is None else Path(base).expanduser().absolute()
    if path.name == "ai" and path.parent.name in {".sico", ".cad"}:
        path = ai_directory({}, temporary=state_path(path, legacy=True), create=True)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    _require_private_directory(path)
    return str(path)


def _runtime_prefix(base: str) -> str:
    """Keep the old runtime prefix only for a legacy project root."""
    path = Path(base)
    return "cad-ai-" if path.name == "ai" and path.parent.name == ".cad" else "sico-ai-"


def _require_private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError(f"runtime path is not a real directory: {path}")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError(f"runtime directory is not private: {path}")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def write_spool(
    spool_dir: Path, prefix: str, data: bytes, *, suffix: str = ""
) -> dict[str, object]:
    if len(data) > MAX_SPOOL_BYTES:
        raise ValueError(f"payload exceeds {MAX_SPOOL_BYTES} bytes")
    if suffix not in {"", ".il", ".ils"}:
        raise ValueError(f"unsupported spool suffix: {suffix}")
    _require_private_directory(spool_dir)
    name = f"{prefix}-{secrets.token_hex(12)}{suffix}"
    path = spool_dir / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {
        "name": name,
        "path": str(path),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def read_spool(spool_dir: Path, name: str, *, max_bytes: int = MAX_SPOOL_BYTES) -> bytes:
    if not name or Path(name).name != name:
        raise ValueError("spool name must be a basename")
    _require_private_directory(spool_dir)
    path = spool_dir / name
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("spool payload is not a regular file (symlink)") from exc
        # Keep ENOENT/ESTALE distinct from invalid file types. The Virtuoso
        # writer can be on another NFS client; only result readers may retry.
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("spool payload is not a regular file")
        if info.st_uid != os.getuid() or info.st_size > max_bytes:
            raise ValueError("spool payload failed ownership or size validation")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(max_bytes + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) != info.st_size or len(data) > max_bytes:
        raise ValueError("spool payload changed while being read")
    return data


__all__ = [
    "MAX_EVAL_BYTES",
    "MAX_SPOOL_BYTES",
    "BRIDGE_CONTAINER_PREFIX",
    "BRIDGE_SPOOL_PREFIX",
    "RuntimePaths",
    "new_token",
    "read_spool",
    "unix_socket_address",
    "write_spool",
]
