"""Evidence-backed audit publication, complete GUI rendering and exact consent."""

from copy import deepcopy
import json

import pytest

from pdk_standard_fixture import package, replace
from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.workspace import select
from cadai.skill_diagnostics import record_output
from test_agent_pdk_choice import pdk_backend, start, tool_data
from test_agent_codex_native import gateway, native_codex
from sico.service.detail_content import DetailContent
from sico.service.pdk_update_review import markdown
from sico.service.workbench_data import WorkbenchService
from sico.service.workbench_query import WorkbenchQuery


def prepare(backend, sessions, tmp_path, capture=None):
    effective, _, _, _ = package(tmp_path, 30)
    start(backend)
    tool_data(backend, 'get_pdk_data', {'library': 'synthetic', 'include_unconfirmed': True})
    sessions[0].standard.updates.source_validator = lambda *_: record_output(capture)
    operations = [replace('/parameters/' + name + '/write', 'allow')
                  for name in effective.document('cell0/cdf.json')['parameters']]
    preview = tool_data(backend, 'prepare_pdk_data_update', {
        'library': 'synthetic', 'revision': effective.revision,
        'changes': [{'file': 'cell0/cdf.json', 'ops': operations}]})
    row = next(r for r in backend.audit.index.audits.values() if r.get('pdk_update_ref'))
    return preview, row


@pytest.mark.parametrize('capture', [None,
    dict(output='', output_capture='skill_ports', output_truncated=False),
    dict(output='Loading test/libInit.il ...\n', output_capture='skill_ports', output_truncated=False),
    dict(output='partial log', output_capture='skill_ports', output_truncated=True)])
def test_full_review_reaches_all_publications_and_consent_is_exact(pdk_backend, tmp_path, capture):
    backend, _, sessions = pdk_backend
    preview, row = prepare(backend, sessions, tmp_path, capture)
    evidence_id = row['evidence'][0]['id']
    retained = backend.audit.index.evidence_value(evidence_id)
    if capture:
        assert all(preview[key] == value for key, value in capture.items())
        assert 'output' not in sessions[0].standard.updates.previews[preview['update_ref']]['result']
    expected = markdown(backend.audit.index, row)
    assert '/parameters/p29/write' in expected and '/parameters/w/write' in expected
    assert DetailContent(backend.audit.index).prepare('audit', row['id'])['markdown'] == expected
    service = WorkbenchService()
    try:
        source = service.source(backend.journal)
        assert source.snapshot().result(5)[1].audits[row['id']]['review_markdown'] == expected
        assert source.query_pages(WorkbenchQuery()).result(5)[1].audits[row['id']]['review_markdown'] == expected
    finally:
        service.close()
        assert service.wait(5)
    backend.audit.answer(row['id'], {'pdk_data_update': {'choice': '确认更新', 'text': ''}},
                         'review-reply', 'owner', row['task_id'])
    backend.audit.deliver_host()
    producer = sessions[0].standard.updates.previews[preview['update_ref']]['result']
    assert backend.audit.pdk_update_confirmation(producer, backend.state.context)['decision'] == 'confirm'
    changed = deepcopy(preview)
    changed['changes'][0]['changes'][0]['after'] = 'deny'
    with pytest.raises(PdkUnavailable, match='exact reviewed'):
        backend.audit.pdk_update_confirmation(changed, backend.state.context)
    assert backend.audit.pdk_update_confirmation(preview, backend.state.context)['decision'] == 'confirm'
    assert tool_data(backend, 'apply_pdk_data_update', {'update_ref': preview['update_ref']})['status'] == 'ok'
    assert backend.audit.index.evidence_value(evidence_id) == retained
    assert all(p['write'] == 'allow' for p in select(tmp_path, 'synthetic', {}).document(
        'cell0/cdf.json')['parameters'].values())


def test_paged_detail_exposes_one_large_review_link(pdk_backend, tmp_path, monkeypatch):
    backend, _, sessions = pdk_backend
    prepare(backend, sessions, tmp_path)
    row = next(r for r in backend.audit.index.audits.values() if r.get("pdk_update_ref"))
    review = {
        "update_ref": row["pdk_update_ref"],
        "revision": row["pdk_update_revision"],
        "page_count": 2,
        "files": 1,
        "fields": 2,
        "pages": [
            {"page": 1, "page_count": 2, "files": ["cell0/cdf.json"],
             "fields": 1, "markdown": "### page 1"},
            {"page": 2, "page_count": 2, "files": ["cell0/cdf.json"],
             "fields": 1, "markdown": "### page 2"},
        ],
    }
    monkeypatch.setattr("sico.service.pdk_update_review.pages",
                        lambda *_args, **_kwargs: review)
    detail = DetailContent(backend.audit.index).prepare("audit", row["id"])
    assert "pdk-review:" not in detail["prefix"]
    assert "右侧详情已分页显示" in detail["prefix"]
    assert detail["review"] == review
    assert detail["markdown"] == "### page 1"


@pytest.mark.parametrize('field', ['changes', 'selection_question', 'schema_version',
                                  'confirmation_required', 'next_action', 'unexpected'])
def test_confirmation_rejects_changed_business_data_before_source_checks(pdk_backend, tmp_path, field):
    backend, _, sessions = pdk_backend
    preview, row = prepare(backend, sessions, tmp_path, dict(
        output='original log', output_capture='skill_ports', output_truncated=False))
    backend.audit.answer(row['id'], {'pdk_data_update': {'choice': '确认更新', 'text': ''}},
                         'review-reply', 'owner', row['task_id'])
    backend.audit.deliver_host()
    updates = sessions[0].standard.updates
    frozen = updates.previews[preview['update_ref']]['result']
    if field == 'changes':
        # Only top-level transport fields are excluded; data named output is still reviewed.
        frozen['changes'][0]['changes'][0]['after'] = {'output': 'changed rule'}
    else:
        frozen[field] = 'changed'
    updates.source_validator = lambda *_: pytest.fail('Invalid confirmation accessed live sources')
    before = (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes()
    reply = backend._execute('apply_pdk_data_update', {'update_ref': preview['update_ref']})
    assert reply['isError']
    error = json.loads(reply['content'][0]['text'])
    assert error['data']['code'] == 'pdk_update_conflict'
    assert 'exact reviewed' in error['data']['message']
    assert (tmp_path / '.sico/ai/pdk-data/index.json').read_bytes() == before


def test_review_still_verifies_original_diagnostic_evidence(pdk_backend, tmp_path):
    backend, _, sessions = pdk_backend
    _, row = prepare(backend, sessions, tmp_path, dict(
        output='original log', output_capture='skill_ports', output_truncated=False))
    evidence = backend.audit.index.data[row['evidence'][0]['id']]
    evidence['value']['data']['output'] = 'tampered log'
    with pytest.raises(ValueError, match='Evidence snapshot has changed'):
        markdown(backend.audit.index, row)


def test_review_is_lossless_and_scrollable_in_audit_panel(pdk_backend, tmp_path):
    from PyQt5.QtWidgets import QApplication, QRadioButton
    from sico_ui.records import AuditPanel
    from sico.service.pdk_update_review import project
    from sico.service.workbench_snapshot import AUDIT_FIELDS

    app = QApplication.instance() or QApplication([])
    backend, _, sessions = pdk_backend
    preview, row = prepare(backend, sessions, tmp_path)
    panel = AuditPanel('无审阅')
    panel.resize(780, 650)
    panel.show()
    shown = project(backend.audit.index, {row['id']: row}, AUDIT_FIELDS)[row['id']]
    try:
        panel.add_audit(shown)
        app.processEvents()
        text = panel.detail.toPlainText()
        for edit in preview['changes'][0]['changes']:
            assert edit['path'] in text
        assert '更新前' in text and '更新后' in text
        assert panel.detail.isVisible() and panel.detail.height() >= 200
        assert panel.detail.verticalScrollBar().maximum() > 0
        assert panel.answer.isVisible()
        assert not any(button.isChecked() for button in panel.choices.findChildren(QRadioButton))
        panel.grab().save(str(tmp_path / 'pdk-review.png'))
    finally:
        panel.close()
        panel.deleteLater()


def test_review_escapes_data_without_hidden_links_or_formatting():
    from PyQt5.QtGui import QTextDocument
    from sico.service.pdk_update_review import cell

    value = 'gate|drain <a href="https://example.invalid">click</a> **rule** `code`\nnext'
    document = QTextDocument()
    document.setMarkdown('| 字段 | 值 |\n| --- | --- |\n| /note | ' + cell(value) + ' |')
    text = document.toPlainText()
    assert 'gate|drain' in text and '**rule**' in text and '`code`' in text
    assert '<a href=' in text and '\\nnext' in text
    assert 'href="https://example.invalid"' not in document.toHtml()
