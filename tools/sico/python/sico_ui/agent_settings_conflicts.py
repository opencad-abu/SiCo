"""Explicit conflict resolution for the project resource default draft."""

from .si_prompt import SiConfirm


class SettingsConflictActions:
    """Draft, latest-default and close confirmations; one confirmation at a time."""

    def confirm(self, text, action, *, closing=False):
        if self.confirmation is not None:
            return
        dialog = self.confirmation = SiConfirm("核对未保存的修改", text, self)
        dialog.add_choice("continue", "继续", default=True)
        dialog.add_choice("cancel", "取消", escape=True)
        def chosen(key):
            self.confirmation = None
            if key == "continue" and (closing or self.current()):
                action()
        dialog.chosen.connect(chosen)
        dialog.finished.connect(lambda _result: self.clear_confirmation(dialog))
        dialog.open()

    def clear_confirmation(self, dialog):
        if self.confirmation is dialog:
            self.confirmation = None
        dialog.deleteLater()

    def choose_latest(self):
        self.confirm("放弃此窗口的草稿，采用刚读取的工程默认？", self.adopt)

    def adopt(self):
        self.draft.accept(self.draft.latest)
        self.ui.message.setText("已采用最新默认。")
        self.render()

    def keep_draft(self):
        self.compare()
        self.confirm("已列出草稿与最新默认的差异。继续保留草稿后，下次保存将以此草稿"
                     "替换最新默认；请先核对其他窗口的修改。", self.rebase)

    def rebase(self):
        self.draft.rebase()
        self.ui.message.setText("已保留草稿，可继续编辑后保存。")
        self.render()
