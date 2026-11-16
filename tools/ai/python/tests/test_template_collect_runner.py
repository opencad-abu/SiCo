"""Batch collector runner: resume identity, failure accounting, report classes."""

import argparse
import json
from pathlib import Path

import pytest

from cadai import template_collect as tc


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def row(library, cell, legacy, wrapper="/src/cds.lib"):
    return dict(library=library, cell=cell, legacy_ref=legacy, batch="B1",
                dataset="D1", recorded_schema="v1", retained_wrapper=wrapper)


DETAIL = dict(template_ref="ctpl_" + "a" * 63, capture_sha256="b" * 64,
              package="/tmp/package", capabilities={"topology-v1": "passed"})


def args(tmp_path, **overrides):
    values = dict(queue=tmp_path / "queue.jsonl", batch="B1",
                  mapping=tmp_path / "mapping.jsonl", session=tmp_path / "session",
                  chunk=2, attempts=2, classifications=None, limit=None,
                  failures_report=None, reset=False)
    values.update(overrides)
    return argparse.Namespace(**values)


def test_targets_and_resume_are_keyed_by_reference(tmp_path):
    rows = [row("lib", "cell", "tpl_1"), dict(row("lib", "cell", "tpl_2"),
                                              assets={"symbol": {"view": "symbol_golden"}}),
            row("lib", "other", "tpl_3")]
    write_rows(tmp_path / "queue.jsonl", rows)
    targets = tc.batch_targets(tmp_path / "queue.jsonl", "B1")
    assert [t["legacy_ref"] for t in targets] == ["tpl_1", "tpl_2", "tpl_3"]
    assert tc.batch_targets(tmp_path / "queue.jsonl", "NONE") == []
    mapping = tmp_path / "mapping.jsonl"
    write_rows(mapping, [dict(legacy_ref="tpl_2", template_ref="ctpl_x")])
    assert set(tc.result_map(mapping)) == {"tpl_2"}


def test_classification_map_and_failure_classes(tmp_path):
    path = tmp_path / "class.jsonl"
    write_rows(path, [dict(legacy_ref="tpl_1", classifications={"M0": {"kind": "pmos"}}),
                      dict(library="lib", cell="c", classifications=[])])
    known = tc.classification_map(path)
    assert known["tpl_1"]["M0"]["kind"] == "pmos"
    assert known["lib/c"] == []
    assert tc.classify_failure("...*Error* Template master unavailable") == "source_missing"
    assert tc.classify_failure("Template dependency exceeds geometry inventory budget") == "budget"
    assert tc.classify_failure("something else") == "other"


def test_run_resumes_and_reports_failures(tmp_path, monkeypatch):
    rows = [row("lib", "a", "tpl_a"), row("lib", "b", "tpl_b"), row("lib", "c", "tpl_c")]
    write_rows(tmp_path / "queue.jsonl", rows)
    calls = []

    def fake_capture_group(directory, source_cds_lib, group, classifications=None):
        calls.append([g["legacy_ref"] for g in group])
        if len(calls) == 1:  # session dies after one capture; b stays in the remainder
            return [(group[0], DETAIL)], group[1:], "HeadlessError: boom"
        if len(calls) == 2:  # b fails both attempts, c captures
            return [(group[1], DETAIL)], group[:1], "HeadlessError: boom"
        return [], group[:1], "HeadlessError: boom"

    monkeypatch.setattr(tc, "capture_group", fake_capture_group)
    report = tmp_path / "failures.json"
    code = tc.run(args(tmp_path, attempts=2, failures_report=report))
    assert code == 1
    stored = tc.result_map(tmp_path / "mapping.jsonl")
    assert set(stored) == {"tpl_a", "tpl_c"}
    payload = json.loads(report.read_text())
    assert payload["requested"] == 3
    assert payload["captured"] == 2
    assert payload["failed"] == 1
    assert set(payload["failures"]) == {"lib/b#tpl_b"}
    assert payload["failure_classes"] == {"other": 1}
    # A resume run retries only the failed row; captured rows are never redone.
    before = len(calls)
    assert tc.run(args(tmp_path, attempts=1)) == 1
    assert calls[before:] == [["tpl_b"]]


def test_run_requires_one_source_list(tmp_path, monkeypatch):
    rows = [row("lib", "a", "tpl_a", wrapper="/w1/cds.lib"),
            row("lib", "b", "tpl_b", wrapper="/w2/cds.lib")]
    write_rows(tmp_path / "queue.jsonl", rows)
    monkeypatch.setattr(tc, "capture_group", lambda *a, **k: ([], [], None))
    with pytest.raises(tc.TemplateError, match="one batch must share"):
        tc.run(args(tmp_path))
