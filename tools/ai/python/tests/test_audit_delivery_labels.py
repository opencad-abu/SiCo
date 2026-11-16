"""Delivery truth wins over automatic-answer presentation in every audit view."""

import pytest
from test_agent_audit_lifecycle import backend, native_input, prepare  # noqa: F401
from sico.codex.rpc import RpcError
from sico.service.chat_audit import audit_status_label, audit_text
from sico_ui.records import AuditPanel, audit_title
from sico_ui.transcript import Transcript


@pytest.mark.parametrize('status,label', [
    ('answer_received', '答复已提交，正在核验'), ('dispatching', '正在发送答复'),
    ('invalid', '答复未交付，问题已失效'), ('unconfirmed', '答复交付未确认'),
    ('answered', '补充事项已按建议继续')])
def test_automatic_status_is_consistent_in_list_card_and_details(status, label):
    row = {'id': 'audit', 'title': 'Review', 'questions': [], 'status': status,
           'review_class': 'supplementary', 'reply': {'automatic': True, 'answers': {}}}
    assert label in audit_status_label(status, automatic=True, has_reply=True,
                                      review_class='supplementary')
    assert label in audit_text('Review', status, [], recommendation='', reason='',
        elicitation=False, recommended_label=lambda x: x, has_reply=True,
        automatic=True, review_class='supplementary')
    assert label in AuditPanel.markdown(row)
    if status in {'invalid', 'unconfirmed'}:
        assert label in audit_title(row) and '已按建议继续' not in audit_title(row)


def test_failed_automatic_delivery_remains_unconfirmed_in_replayed_transcript(backend):
    loop, sent = backend
    row = prepare(loop, review_class='supplementary')
    loop.audit.open(native_input(row['questions']))
    def fail(*args, **kwargs):
        raise OSError('connection lost')
    loop.runtime.rpc.send = fail
    with pytest.raises(RpcError, match='unconfirmed'):
        loop.audit.answer_supplementary()
    record = loop.audit.index.audits[row['audit_id']]
    assert record['status'] == 'unconfirmed' and not sent
    transcript = Transcript()
    for event in loop.journal.events():
        transcript.receive(event)
    for value in (audit_title(record), AuditPanel.markdown(record),
                  transcript.audits[row['audit_id']]['message']['text']):
        assert '答复交付未确认' in value and '已按建议继续' not in value
