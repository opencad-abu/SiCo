"""Read-only auxiliary dialogs opened from the settings window."""


class SettingsAuxiliaryDialogs:
    """Diff, isolated resource check and knowledge preview; one window per view."""

    def compare(self):
        from .agent_settings_diff import show_diff

        if self.draft.latest:
            before, after = self.draft.latest["manifest"], self.draft.manifest
            labels = ("最新工程默认", "当前草稿")
        else:
            before = self.response["session"]["snapshot"]["configuration"]["effective"]["manifest"]
            after = self.response["selected_defaults"]["manifest"]
            scope = "会话" if self.response.get("defaults_scope") == "session" else "服务"
            labels = ("当前会话选择", scope + "来源默认")
        self.diff_dialog = show_diff(self, before, after, labels)

    def discard_close(self):
        self.discard = True
        self.close()

    def displayed_manifest(self):
        mode = self.ui.mode.currentData()
        if mode == "project_defaults":
            return self.draft.manifest
        if mode == "selected_defaults":
            return self.response.get("selected_defaults", {}).get("manifest")
        return (self.response.get("session") or {}).get("snapshot", {}).get(
            "configuration", {}).get("effective", {}).get("manifest")

    def check_resources(self):
        from .agent_resource_check import ResourceCheckDialog

        previous = getattr(self, "check_dialog", None)
        if previous is not None:
            previous.raise_()
            return
        manifest = self.displayed_manifest()
        if manifest and self.current() and not self.busy:
            dialog = self.check_dialog = ResourceCheckDialog(self, manifest)
            dialog.finished.connect(lambda _result: self.clear_check(dialog))
            dialog.open()

    def clear_check(self, dialog):
        self.check_dialog = None
        dialog.deleteLater()

    def preview_knowledge(self):
        from .knowledge_preview import KnowledgePreview

        previous = getattr(self, "knowledge_dialog", None)
        if previous is not None:
            previous.raise_()
            return
        manifest = self.displayed_manifest()
        if manifest and self.current() and not self.busy:
            dialog = self.knowledge_dialog = KnowledgePreview(self, manifest)
            def finished(_result):
                self.knowledge_dialog = None
                dialog.deleteLater()
            dialog.finished.connect(finished)
            dialog.open()
