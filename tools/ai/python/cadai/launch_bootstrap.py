"""Private Codex home filesystem preparation, performed only when requested."""

from __future__ import annotations

from sicoenv import read as environment_setting

import os
import stat
from pathlib import Path

from .codex_selection import require_supported_runtime


def private_codex_home() -> Path:
    require_supported_runtime()
    explicit = environment_setting(os.environ, "SICO_CODEX_HOME")
    if "SICO_CODEX_HOME" in os.environ and not explicit:
        raise ValueError("SICO_CODEX_HOME must not be empty")
    if explicit:
        home = Path(explicit).expanduser()
    else:
        state = os.environ.get("XDG_STATE_HOME")
        base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
        # Keep the existing dedicated Codex login namespace stable across the
        # agent-neutral product rename.
        home = base / "cad-codex" / "codex-home"
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = home.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeError(f"CAD Codex home must be a real directory: {home}")
    if info.st_uid != os.getuid():
        raise RuntimeError(f"CAD Codex home is owned by another user: {home}")
    if info.st_mode & 0o077:
        os.chmod(home, 0o700)
    return home.resolve()


def install_packaged_codex_skills(codex_home: Path, install_root: Path) -> None:
    """Expose release-owned Codex skills through the private Codex home."""
    packaged_root = (install_root / "skills").resolve()
    if not packaged_root.is_dir():
        # Upgrading an existing private home must not retain an old release's
        # knowledge assets. Only remove our managed symlink, never user content.
        skills_root = codex_home / "skills"
        if skills_root.is_dir() and not skills_root.is_symlink():
            link = skills_root / "sico-cad-ai"
            if link.is_symlink():
                link.unlink()
        return
    skills_root = codex_home / "skills"
    if skills_root.exists() or skills_root.is_symlink():
        info = skills_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError(f"Codex skills root must be a real directory: {skills_root}")
    else:
        skills_root.mkdir(mode=0o700)
    link = skills_root / "sico-cad-ai"
    if link.exists() or link.is_symlink():
        if not link.is_symlink():
            raise RuntimeError(f"refusing to replace existing Codex skill path: {link}")
        if link.resolve() == packaged_root:
            return
        link.unlink()
    link.symlink_to(packaged_root, target_is_directory=True)
