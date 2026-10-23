"""Read-only differences between two resource selections."""

import difflib

from PyQt5.QtWidgets import QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from sico.agent_resources.schema import upgrade_manifest
from .chrome import SiDialog


def show_diff(parent, before, after, labels):
    def lines(value):
        manifest = upgrade_manifest(value)
        result = []
        for kind in sorted(manifest):
            if kind == "version":
                continue
            for row in sorted(manifest[kind], key=lambda r: r.get("id", r.get("path", ""))):
                result.append(kind + " / " + str(row.get("id", row.get("path", ""))) + "\n")
                result.extend("  " + key + ": " + str(row[key]) + "\n" for key in sorted(row))
        return result
    text = "".join(difflib.unified_diff(lines(before), lines(after),
                                      fromfile=labels[0], tofile=labels[1]))
    dialog = SiDialog("资源配置差异", parent)
    dialog.resize(780, 560)
    layout = QVBoxLayout()
    dialog.content_layout().addLayout(layout)
    layout.addWidget(QLabel("“-” 为原值，“+” 为新值。此视图只读。"))
    content = QPlainTextEdit()
    content.setReadOnly(True)
    content.setPlainText(text or "两份资源选择相同。")
    layout.addWidget(content)
    close = QPushButton("关闭")
    close.clicked.connect(lambda _checked=False: dialog.close())
    layout.addWidget(close)
    dialog.show()
    return dialog
