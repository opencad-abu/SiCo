"""Public mapping batches reach the host's exact reviewed confirmation surface."""

from copy import deepcopy

import pytest
from pdk_draft_fixture import entry, setup, source
from pdk_standard_fixture import replace
from sico.service.pdk_update_review import markdown
from test_agent_codex_native import gateway, native_codex  # noqa: F401
from test_agent_pdk_choice import pdk_backend, start, tool_data  # noqa: F401

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_standard.updates import Updates
from cadai.pdk_standard.workspace import select


def test_mapping_review_is_complete_and_restart_keeps_real_host_answer(pdk_backend, tmp_path):  # noqa: F811
    backend, _, sessions = pdk_backend
    _, bridge, _ = setup(tmp_path)
    start(backend)
    tool_data(backend, "get_pdk_data", {"library": "synthetic", "include_unconfirmed": True})
    standard = sessions[0].standard
    standard.drafts.bridge = bridge
    from cadai.pdk_standard.freshness import check

    standard.updates.source_validator = lambda p, d: check(bridge, p, d)
    draft = tool_data(
        backend,
        "prepare_pdk_parameter_draft",
        {
            "library": "synthetic",
            "revision": select(tmp_path, "synthetic", {}).revision,
            "source": source(),
            "entries": [entry(relation="Per-finger input, no multiplier")],
        },
    )
    review = tool_data(
        backend,
        "submit_pdk_parameter_batch",
        {
            "draft_ref": draft["draft_ref"],
            "draft_version": draft["draft_version"],
            "revision": draft["revision"],
            "batch_id": "mos",
            "entry_ids": ["width"],
            "changes": [
                {"file": "cell0/cdf.json", "ops": [replace("/parameters/w/write", "allow")]}
            ],
        },
    )
    row = next(r for r in backend.audit.index.audits.values() if r.get("pdk_update_ref"))
    from PyQt5.QtGui import QTextDocument

    document = QTextDocument()
    document.setMarkdown(markdown(backend.audit.index, row))
    rendered = document.toPlainText()
    for value in (
        "manual.txt",
        "Width of one channel",
        "Test width",
        "单指宽度",
        "Per",
        "locator",
        "/parameters/w/write",
        "fingerprint",
    ):
        assert value in rendered
    backend.audit.answer(
        row["id"],
        {"pdk_data_update": {"choice": "确认更新", "text": ""}},
        "review-reply",
        "owner",
        row["task_id"],
    )
    backend.audit.deliver_host()
    changed = deepcopy(review)
    changed["mapping"]["entries"][0]["meaning"] = "Other meaning"
    with pytest.raises(PdkUnavailable, match="exact reviewed"):
        backend.audit.pdk_update_confirmation(changed, backend.state.context)
    standard.updates = Updates(tmp_path, {})
    standard.updates.source_validator = lambda p, d: check(bridge, p, d)
    assert (
        tool_data(backend, "apply_pdk_data_update", {"update_ref": review["update_ref"]})["status"]
        == "ok"
    )
    batches = tool_data(
        backend, "get_pdk_parameter_draft", {"draft_ref": draft["draft_ref"], "section": "batches"}
    )
    assert batches["items"][0]["status"] == "applied"
