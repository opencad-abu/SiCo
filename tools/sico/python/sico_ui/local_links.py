"""Open explicit local-file anchors without treating text highlights as links."""

from pathlib import Path

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices

__all__ = ("local_file_path", "open_local_file")


def local_file_path(href):
    """Return the absolute path named by an explicit local href, or ``None``."""
    if not isinstance(href, str) or not href or href.isspace():
        return None
    url = QUrl(href)
    if url.isLocalFile():
        value = url.toLocalFile()
    elif url.scheme() or "://" in href:
        return None
    else:
        value = href
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    return path if path.is_absolute() else None


def open_local_file(path):
    """Hand an existing regular local file to the desktop, returning success."""
    path = Path(path)
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
