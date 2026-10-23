"""Bounded text filtering and keyboard-accessible labels for the task tree."""

from PyQt5.QtWidgets import QInputDialog


class TaskFilterMixin:
    def set_filter(self, text):
        self._filter_text = str(text or "").strip().casefold()
        self._apply_filter()

    def _apply_filter(self):
        needle = self._filter_text

        def visit(item):
            text = (item.text(0) + " " + item.toolTip(0)).casefold()
            children = [visit(item.child(index)) for index in range(item.childCount())]
            item.setHidden(not ((not needle or needle in text) or any(children)))
            return not item.isHidden()

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index))

    def _ask_filter(self):
        text, accepted = QInputDialog.getText(
            self, "筛选任务", "任务、阶段或请求关键字：", text=self._filter_text)
        if accepted:
            self.set_filter(text)
