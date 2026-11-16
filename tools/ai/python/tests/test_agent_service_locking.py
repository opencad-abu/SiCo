"""Mount policy checks prevent local-only NAS locks from electing multiple services."""

from io import StringIO
from pathlib import Path

import pytest

from sico.storage.project_files import RegistryError
from sico.storage.project_locking import validate_locking


@pytest.mark.parametrize("filesystem,options,accepted", [
    ("xfs", "rw", True), ("tmpfs", "rw", True), ("nfs4", "rw,local_lock=none", True),
    ("nfs", "rw,vers=3,local_lock=posix", True), ("nfs", "rw,nolock", False),
    ("nfs4", "rw,local_lock=flock", False), ("nfs4", "rw,local_lock=all", False),
    ("cifs", "rw", False), ("fuse.unknown", "rw", False), ("ext4", "ro", False),
])
def test_mount_policy(tmp_path, monkeypatch, filesystem, options, accepted):
    text = "1 0 0:1 / / rw - ext4 /dev/root rw\n"
    text += f"2 1 0:2 / {tmp_path} rw - {filesystem} server:/project {options}\n"
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: StringIO(text))
    if accepted:
        validate_locking(tmp_path)
    else:
        with pytest.raises(RegistryError, match="flock policy"):
            validate_locking(tmp_path)


def test_alias_and_escaped_mount_path_select_the_actual_mount(tmp_path, monkeypatch):
    target = tmp_path / "with space"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target)
    escaped = str(target).replace(" ", r"\040")
    text = "1 0 0:1 / / rw - xfs /dev/root rw\n"
    text += f"2 1 0:2 / {escaped} rw - nfs server:/project rw,local_lock=all\n"
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: StringIO(text))
    with pytest.raises(RegistryError, match="flock policy"):
        validate_locking(alias)


@pytest.mark.parametrize("source", ["", "invalid line", "x" * (1024 * 1024 + 1)])
def test_unknown_or_invalid_mount_information_fails_closed(tmp_path, monkeypatch, source):
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: StringIO(source))
    with pytest.raises(RegistryError):
        validate_locking(tmp_path)
