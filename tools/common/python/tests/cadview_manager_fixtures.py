"""Filesystem fixture for manager contract cases."""

from pathlib import Path


def _cds(tmp_path: Path) -> Path:
    library = tmp_path / "work"
    library.mkdir()
    path = tmp_path / "cds.lib"
    path.write_text("DEFINE work ./work\n", encoding="utf-8")
    return path
