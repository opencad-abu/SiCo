"""Full reviews fit the audit surface; oversized reviews never access live sources."""

import json

import pytest

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.jsonio import encode
from cadai.pdk_standard.review import MAX_REVIEW_BYTES, validate_scope
from cadai.pdk_standard.updates import Updates
from cadai.pdk_standard.workspace import select
from pdk_standard_fixture import package, replace
from sico.service.pdk_update_review import pages


def test_large_cdf_preview_has_short_question_and_complete_frozen_evidence(tmp_path):
    effective, _, _, _ = package(tmp_path, 30)
    operations = [replace('/parameters/' + name + '/write', 'allow')
                  for name in effective.document('cell0/cdf.json')['parameters']]
    updates = Updates(tmp_path, {})
    checked = []
    updates.source_validator = lambda package, diffs: checked.append(diffs)
    result = updates.prepare({'library': 'synthetic', 'revision': effective.revision,
                              'changes': [{'file': 'cell0/cdf.json', 'ops': operations}]})
    assert len(json.dumps(result['changes'], separators=(',', ':'))) > 1750
    assert len(result['selection_question']['question']) < 2000
    assert len(result['changes'][0]['changes']) == 30
    assert checked == [result['changes']]
    scope = json.loads(updates.previews[result['update_ref']]['scope'].read_text())
    assert scope['changes'] == result['changes']
    assert updates.apply({'update_ref': result['update_ref']})['confirmation_required']
    assert select(tmp_path, 'synthetic', {}).revision == effective.revision


def test_large_review_is_retained_and_paginated(tmp_path):
    effective, _, _, _ = package(tmp_path, 90)
    updates = Updates(tmp_path, {})
    checked = []
    updates.source_validator = lambda _package, diffs: checked.append(diffs)
    operations = [replace('/parameters/' + name + '/write', 'allow')
                  for name in effective.document('cell0/cdf.json')['parameters']]
    result = updates.prepare({'library': 'synthetic', 'revision': effective.revision,
                              'changes': [{'file': 'cell0/cdf.json', 'ops': operations}]})
    assert len(json.dumps(result['changes'], ensure_ascii=False)) > 5000
    assert checked == [result['changes']]
    assert updates.apply({'update_ref': result['update_ref']})['confirmation_required']

    class Index:
        def verify_evidence(self, _evidence):
            return None

        def evidence_value(self, _ref):
            return {"data": {
                "update_ref": result["update_ref"],
                "revision": effective.revision,
                "changes": result["changes"],
            }}

    review = pages(Index(), {
        "pdk_update_ref": result["update_ref"],
        "pdk_update_revision": effective.revision,
        "evidence": [{"id": "d_review"}],
    }, page_bytes=4096)
    assert review["page_count"] > 1
    assert review["files"] == 1 and review["fields"] == 90
    assert sum(page["fields"] for page in review["pages"]) == 90
    assert all(page["complete"] for page in review["pages"])


def test_review_byte_limit_counts_utf8_and_accepts_exact_boundary():
    scope = {'library': 'test', 'revision': 'r', 'changes': [
        {'file': 'device.json', 'changes': [{'path': '/note', 'after': '工艺'}]}]}
    edit = scope['changes'][0]['changes'][0]
    edit['after'] += 'x' * (MAX_REVIEW_BYTES - len(encode(scope)))
    assert len(encode(scope)) == MAX_REVIEW_BYTES
    validate_scope(scope)
    edit['after'] += 'x'
    with pytest.raises(PdkUnavailable, match=str(MAX_REVIEW_BYTES + 1) + ' bytes'):
        validate_scope(scope)
