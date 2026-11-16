"""Long PDK reviews page inside the right detail renderer."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from sico_ui.workbench_view import WorkbenchDetail


def test_long_review_is_paged_in_the_right_detail():
    app = QApplication.instance() or QApplication([])
    review = {
        "update_ref": "pdk-update:fixture",
        "revision": "rev-1",
        "page_count": 2,
        "files": 1,
        "fields": 2,
        "pages": [
            {"page": 1, "page_count": 2, "files": ["device.json"], "fields": 1,
             "markdown": "### page 1\n\n| field | before | after |\n"
                         "| --- | --- | --- |\n| /a | old | new |"},
            {"page": 2, "page_count": 2, "files": ["device.json"], "fields": 1,
             "markdown": "### page 2\n\n| field | before | after |\n"
                         "| --- | --- | --- |\n| /b | old | new |"},
        ],
    }
    detail = WorkbenchDetail()
    detail.current = ("audit", "fixture")
    detail._detail_generation = 0
    try:
        detail._render_detail(0, {
            "title": "PDK review", "prefix": "<p>summary</p>",
            "markdown": review["pages"][0]["markdown"], "suffix": "",
            "images": [], "external_urls": [], "review": review,
        })
        app.processEvents()
        assert not detail.review_bar.isHidden()
        assert detail.review_pages.count() == 2
        assert "page 1" in detail.document.toPlainText()
        detail.review_pages.setCurrentIndex(1)
        app.processEvents()
        assert "page 2" in detail.document.toPlainText()
        detail.review_search.setText("page 2")
        app.processEvents()
        assert detail.review_pages.currentIndex() == 1
    finally:
        detail.close()
