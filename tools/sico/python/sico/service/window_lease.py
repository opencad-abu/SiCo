"""One SiCo window per project: the desktop process holds this lock while it lives."""

from ..storage.project_files import window_directory
from ..storage.project_lock import ProjectLock

MESSAGE = "此工程已有一个 SiCo 窗口在运行；请使用那个窗口，或先关掉它。"


class WindowAlreadyOpen(RuntimeError):
    """Another live SiCo window owns this project's desktop lock."""

    def __init__(self, message=MESSAGE):
        super().__init__(message)


class WindowLease:
    """The desktop lock of one window; the OS releases it when a crashed window dies."""

    def __init__(self, project):
        self._lock = ProjectLock(window_directory(project, create=True), create=True)

    def close(self):
        self._lock.close()


def claim_window(project):
    """Claim this project's window lock; a second live window is refused, not queued."""

    try:
        return WindowLease(project)
    except BlockingIOError as exc:
        raise WindowAlreadyOpen() from exc
