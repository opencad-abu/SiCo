"""Open one single-purpose thread-control dialog."""

from .goal_controls import GoalDialog
from .history_controls import HistoryDialog
from .memory_controls import MemoryDialog

DIALOGS = {"goal": GoalDialog, "history": HistoryDialog, "memory": MemoryDialog}


def show_thread_controls(window, *, page="goal"):
    dialog = DIALOGS.get(page)
    if dialog is None:
        raise ValueError("Unknown thread control page: " + str(page))
    if window.page.reviewing is not None or window.page.opening:
        window.info_app.notice("无法操作会话", "请先进入要操作的会话")
        return
    previous = getattr(window, "thread_dialog", None)
    if previous is not None:
        previous.close()
        previous.deleteLater()
    window.thread_dialog = dialog(window)
    window.thread_dialog.show()
