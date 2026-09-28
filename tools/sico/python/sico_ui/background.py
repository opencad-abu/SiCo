"""Background saved-design inspections, isolated from foreground conversation work."""
from sico.service.metadata import format_result_json
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .presentation import BusyCursor
from .receipts import DataReceipt

STATES = {'accepted': '已接收', 'queued': '排队中', 'starting': '启动中', 'ready': '已就绪',
          'running': '检查中', 'completed': '已完成', 'cancelled': '已取消（未派发）',
          'cancelling': '正在取消', 'running_unknown': '执行结果未确认',
          'background_unavailable': '后台不可用', 'worker_timeout': '执行超时',
          'cleanup_failed': '资源回收失败', 'interrupted': '执行中断，未重试',
          'recovering': '正在核对并回收后台资源', 'result_invalid': '结果校验失败，未重试',
          'queue_timeout': '排队超时（未派发）'}


class BackgroundPanel(QWidget):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.scope = None
        self.rows = {}
        self.pending_command = False
        layout = QVBoxLayout(self)
        self.target = QLabel()
        self.target.setTextFormat(Qt.PlainText)
        self.target.setWordWrap(True)
        layout.addWidget(self.target)
        hint = QLabel('在独立 Virtuoso 中检查已保存的原理图；结果不包含前台未保存的修改。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        actions = QHBoxLayout()
        self.submit_button = QPushButton('后台检查当前原理图')
        self.cancel_button = QPushButton('取消所选任务')
        self.read_button = QPushButton('查看校验结果')
        for button, callback in ((self.submit_button, self.submit),
                                 (self.cancel_button, self.cancel), (self.read_button, self.read_result)):
            actions.addWidget(button)
            button.clicked.connect(callback)
        layout.addLayout(actions)
        self.jobs = QTreeWidget()
        self.jobs.setHeaderLabels(['设计', '状态', '提交时间'])
        self.jobs.setRootIsDecorated(False)
        self.jobs.currentItemChanged.connect(self.selection_changed)
        layout.addWidget(self.jobs, 1)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText('选中已完成的任务，查看校验后的结果。')
        layout.addWidget(self.result, 1)
        self.poll_receipt = DataReceipt(self)
        self.command_receipt = DataReceipt(self)
        self.result_receipt = DataReceipt(self)
        # 提交/取消与读取校验结果期间，busy 只出现在对应的控件里；轮询不占用光标。
        self._jobs_busy = BusyCursor(self.jobs)
        self._result_busy = BusyCursor(self.result)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.sync() if self.isVisible() else None)
        self.timer.start(500)
        self.refresh_controls()

    def current_scope(self):
        controller = self.window.page.session
        return (controller.session_id, controller.runtime_id, self.window.page.reviewing,
                self.window.page.activation)

    def usable(self):
        return (not self.window.lifecycle.closing and self.window.page.reviewing is None
                and not self.window.page.opening)

    def sync(self):
        scope = self.current_scope()
        if scope != self.scope:
            self.scope = scope
            for receipt in (self.poll_receipt, self.command_receipt, self.result_receipt):
                receipt.clear()
            self._jobs_busy.set(False)
            self._result_busy.set(False)
            self.pending_command = False
            self.rows = {}
            self.jobs.clear()
            self.result.clear()
            self.notice.clear()
        self.refresh_controls()
        if self.usable():
            self.poll()

    def _request(self, method, *args, **kwargs):
        return self.window.api.background_request(self.window.page.session, method, *args, **kwargs)

    def poll(self):
        if self.poll_receipt.pending or not self.usable():
            return
        scope = self.scope
        try:
            self.poll_receipt.watch(self._request('list_background'),
                lambda rows: self.render(rows) if scope == self.current_scope() else None,
                lambda exc: self.failed(exc) if scope == self.current_scope() else None)
        except Exception as exc:
            self.failed(exc)

    def render(self, rows):
        selected = self.selected_id()
        previous = self.rows.get(selected, {}).get('artifact')
        self.rows = {row['job_id']: row for row in rows}
        self.jobs.blockSignals(True)
        self.jobs.clear()
        for row in rows:
            design = '/'.join(str(row.get('target', {}).get(k, '')) for k in ('lib', 'cell', 'view'))
            when = row.get('accepted_at')
            item = QTreeWidgetItem([design, STATES.get(row['state'], row['state']),
                datetime.fromtimestamp(when).strftime('%m-%d %H:%M:%S') if when else ''])
            item.setData(0, Qt.UserRole, row['job_id'])
            item.setToolTip(0, 'Job: ' + row['job_id'] + '\nWorker: ' + row.get('worker_id', ''))
            item.setToolTip(1, row.get('reason', ''))
            self.jobs.addTopLevelItem(item)
            if row['job_id'] == selected:
                self.jobs.setCurrentItem(item)
        self.jobs.blockSignals(False)
        if (selected != self.selected_id()
                or previous != self.rows.get(self.selected_id(), {}).get('artifact')):
            self.selection_changed()
        self.refresh_controls()

    def selected_id(self):
        item = self.jobs.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def selection_changed(self, *_args):
        self.result_receipt.clear()
        self.result.clear()
        self.refresh_controls()

    def refresh_controls(self):
        cell = self.window.presentation.submission_context.snapshot.get('cellview') or {}
        self.target.setText('当前设计：' + '/'.join(str(cell.get(k, '')) for k in ('lib', 'cell', 'view')))
        row = self.rows.get(self.selected_id(), {})
        usable = self.usable() and not self.pending_command
        writable = usable and (not hasattr(self.window.api, 'can_control')
                               or self.window.api.can_control(self.window.page.session))
        # Custom schematic view names are checked by the worker against actual OA type.
        schematic = all(cell.get(k) for k in ('lib', 'cell', 'view')) and cell.get('view') != 'layout'
        self.submit_button.setEnabled(bool(writable and schematic))
        self.cancel_button.setEnabled(writable and row.get('control_available', True)
            and row.get('state') in {'accepted', 'queued', 'starting', 'ready',
                                    'running', 'running_unknown'})
        self.cancel_button.setToolTip('请在提交任务的桌面中取消' if row.get('control_available') is False else '')
        self.read_button.setEnabled(usable and row.get('state') == 'completed')

    def _command(self, method, *args, **kwargs):
        if not self.usable() or self.pending_command:
            return
        scope = self.scope
        self.pending_command = True
        self.notice.setText('正在提交…' if method == 'submit_background' else '正在请求取消…')
        self._jobs_busy.set(True)
        self.refresh_controls()
        def done(row):
            self._jobs_busy.set(False)
            if scope != self.current_scope():
                return
            self.pending_command = False
            self.notice.setText(STATES.get(row['state'], row['state']))
            self.poll_receipt.clear()
            self.render([row] + [old for key, old in self.rows.items() if key != row['job_id']])
        def failed(exc):
            self._jobs_busy.set(False)
            if scope == self.current_scope():
                self.pending_command = False
                self.failed(exc)
        try:
            self.command_receipt.watch(self._request(method, *args, **kwargs), done, failed)
        except Exception as exc:
            failed(exc)

    def submit(self, _checked=False):
        if not self.submit_button.isEnabled():
            return
        origin = self.window.presentation.submission_context
        target = dict(origin.snapshot['cellview'])
        self._command('submit_background', 'inspect_schematic', target, origin=origin)

    def cancel(self, _checked=False):
        if self.cancel_button.isEnabled():
            self._command('cancel_background', self.selected_id())

    def read_result(self, _checked=False):
        if not self.read_button.isEnabled():
            return
        scope, key = self.scope, self.selected_id()
        row = self.rows[key]
        self.result.clear()
        self.notice.setText('正在校验结果…')
        self._result_busy.set(True)
        def current():
            return scope == self.current_scope() and key == self.selected_id()
        def done(result):
            self._result_busy.set(False)
            if current():
                self.notice.setText('结果校验通过 · 已保存的设计')
                displayed = {key: result[key] for key in ('inspection', 'generated_at', 'snapshot_scope')
                             if key in result}
                displayed.update(result)
                self.result.setPlainText(format_result_json(displayed))
        def failed(exc):
            self._result_busy.set(False)
            if current():
                self.failed(exc)
        try:
            self.result_receipt.watch(self._request('read_background_result', key, row['artifact']), done,
                failed)
        except Exception as exc:
            self.failed(exc)

    def showEvent(self, event):
        super().showEvent(event)
        self.sync()

    def failed(self, exc):
        self._jobs_busy.set(False)
        self._result_busy.set(False)
        self.notice.setText(str(exc))
        self.refresh_controls()

    def closeEvent(self, event):
        self.timer.stop()
        for receipt in (self.poll_receipt, self.command_receipt, self.result_receipt):
            receipt.clear()
        super().closeEvent(event)
