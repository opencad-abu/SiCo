"""Visual review bundles must record source/generated artifacts and label deltas."""

import json

from test_draw_symbol import gate_record

from cadai.template_build import publish
from cadai.template_symbol_visual_diff import run


def test_visual_diff_bundle_records_allowed_differences_and_screenshot(tmp_path):
    record = gate_record("INVX1", labels=True)
    catalog = tmp_path / "catalog.sqlite3"
    publish([record], catalog)
    screenshot = tmp_path / "cadence.png"
    screenshot.write_bytes(b"fake screenshot for manifest plumbing")
    screenshot_manifest = tmp_path / "screenshots.json"
    screenshot_manifest.write_text(
        json.dumps(
            {
                "format": "sico.symbol.screenshot.v1",
                "screenshots": [{"template_ref": record["template_ref"], "path": str(screenshot)}],
            }
        )
    )
    output = tmp_path / "review"
    report = run(catalog, output, [record["template_ref"]], screenshot_manifest)
    assert report["passed"]
    assert report["templates"] == 1
    item = report["items"][0]
    assert item["style"] == "template_outline"
    assert item["label_policy"] == "preserve"
    assert item["review_status"] == "pending_human"
    assert item["unexpected_labels"] == []
    assert any(row["category"] == "preserved" for row in item["allowed_differences"])
    assert (output / record["template_ref"] / "visual-diff.html").is_file()
    assert (output / "index.html").is_file()
    assert (output / record["template_ref"] / item["screenshot"]).is_file()


def test_visual_diff_without_screenshot_is_explicitly_pending(tmp_path):
    record = gate_record("INVX1")
    catalog = tmp_path / "catalog.sqlite3"
    publish([record], catalog)
    report = run(catalog, tmp_path / "review", [record["template_ref"]])
    assert report["passed"]
    assert report["review_pending"] == 1
    assert report["items"][0]["review_status"] == "pending_screenshot"
