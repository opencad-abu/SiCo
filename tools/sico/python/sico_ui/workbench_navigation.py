"""Passive detail navigation and receipts bound to one source and page lifetime."""

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices

from sico.core.links import parse_link
from sico.service.workbench_data import DetailLinkError


class DetailNavigation:
    def open_url(self, url):
        if url in self.external_urls:
            return QDesktopServices.openUrl(QUrl(url))
        try:
            kind, key = parse_link(url, self.index.session_id)
        except (ValueError, AttributeError):
            self.show_notice("此链接未登记在当前会话中。")
            return False
        self.open(kind, key, parent=self.current)
        return True

    def invalidate(self):
        """Invalidate without touching Qt; also safe during QObject destruction."""
        self._active = False
        self._detail_generation += 1

    def open(self, kind, key, *, remember=True, position=0, parent=None, notice=""):
        if not self._active:
            return
        previous = self.current
        previous_position = self.document.verticalScrollBar().value()
        previous_history = list(self.history)
        if remember and self.current:
            self.history.append((*self.current, self.document.verticalScrollBar().value()))
            self.history = self.history[-100:]
        self.current = (kind, key)
        self.external_urls.clear()
        self.image_sizes.clear()
        self.show_notice("")
        self._detail_generation += 1
        generation = self._detail_generation
        self._detail_receipt.clear()
        self.document.setPlainText("正在读取详情…")
        self.document.set_busy(True)

        source = self.index.source

        def current():
            if (not self._active or generation != self._detail_generation
                    or source is not self.index.source):
                return False
            if source.scope is not None:
                try:
                    source.scope.validate_current()
                except ValueError:
                    return False
            return True

        def failed(exc):
            if not current():
                return
            if isinstance(exc, DetailLinkError) and previous is not None:
                self.history = previous_history
                self.open(*previous, remember=False, position=previous_position,
                          notice="此链接未登记在当前报告中。")
                return
            self._render_detail(generation, {
                "title": "", "notice": "数据暂不可读或校验失败，原始记录保留。", "html": "",
            })

        def ready(result):
            if current():
                source.validate_current()
                if notice:
                    result = {**result, "notice": notice}
                self._render_detail(generation, result, position)
                self.objectShown.emit(kind, key)

        try:
            future = self.index.source.detail(
                kind, key, compact=self.document.viewport().width() < 600, parent=parent,
            )
        except (ValueError, RuntimeError) as exc:
            failed(exc)
            return
        self._detail_receipt.watch(
            future,
            ready,
            failed,
        )

