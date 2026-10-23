"""Read-only preview products for the UI viewer; contents never reach the model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cadai.circuit_preview_catalog import list_preview_artifacts, read_preview_artifact


@dataclass(frozen=True)
class PreviewEntry:
    """One listed preview product; the file path stays in the service."""

    name: str
    size: int
    mtime: int
    sha256: str = ""
    target: str = ""
    registered: bool = False
    notice: str = ""


@dataclass(frozen=True)
class PreviewIndex:
    """One workspace's listed preview products, never their bytes."""

    rows: tuple
    truncated: bool = False
    notice: str = ""


@dataclass(frozen=True)
class PreviewContent:
    """One verified preview image: filename, digest and bytes, or a notice."""

    name: str = ""
    target: str = ""
    sha256: str = ""
    data: bytes = b""
    notice: str = ""

    @property
    def readable(self):
        return bool(self.data)


def workspace_of(reader):
    """The workspace owns the state root that holds the preview directory."""
    return Path(reader.root).parents[2]  # workspace/.sico/ai/agent


def preview_index(reader):
    """List the workspace's preview products; a manual export is not an error."""
    try:
        catalog = list_preview_artifacts(workspace_of(reader))
    except (OSError, ValueError, KeyError, TypeError):
        return PreviewIndex((), notice="预览产物目录暂不可读。")
    notice = "预览产物较多，仅显示最近的一部分。" if catalog.truncated else ""
    rows = tuple(PreviewEntry(row.name, row.size, int(row.mtime * 1_000_000_000), row.sha256,
                              row.target, row.registered, row.notice) for row in catalog.rows)
    return PreviewIndex(rows, catalog.truncated, notice)


def read_preview(reader, name):
    """Read one listed preview by name; failures stay in the notice."""
    try:
        artifact = read_preview_artifact(workspace_of(reader), name)
    except (OSError, ValueError, KeyError, TypeError):
        return PreviewContent(notice="该预览产物暂不可读或校验失败；文件保留在预览目录。")
    # The content digest covers what is shown; a stale manifest only blocks verification.
    return PreviewContent(artifact.name, artifact.target, artifact.digest, artifact.data,
                          artifact.notice)
