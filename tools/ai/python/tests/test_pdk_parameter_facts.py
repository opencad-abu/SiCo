"""Facts are source checked, scoped and atomic; policy remains host-owned."""

import pytest
from pdk_draft_fixture import approve, create, setup, submit
from pdk_fixture import raw

from cadai.pdk_errors import PdkUnavailable
from cadai.pdk_session import PdkSession
from cadai.pdk_standard.jsonio import LIMIT, encode
from cadai.pdk_standard.workspace import select


def fixture(tmp_path, count=2):
    service, bridge, manual = setup(tmp_path, count)
    approve(service, submit(service, create(service)))
    session = PdkSession(
        bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"}
    )
    return session, bridge, manual


def fact(field="domain", **extra):
    return {
        "id": "width-" + field,
        "device": "cell0",
        "parameter": "w",
        "field": field,
        "disposition": "established",
        "basis": "input_constraint" if field in {"domain", "grid"} else "definition",
        "source": {
            "file_ref": "manual",
            "document_revision": "v1",
            "locator": {"page": 1, "section": "width"},
        },
        "reason": "Synthetic complete input specification",
        **(
            {
                "value": {"kind": "interval", "min": 100000, "max": 500000},
                "input_unit": "um",
                "complete": True,
                "grid_status": "none",
            }
            if field == "domain"
            else {}
        ),
        **extra,
    }


def args(session, facts=None, request="test"):
    return {
        "library": "synthetic",
        "revision": select(session.standard.workspace, "synthetic", {}).revision,
        "request_id": request,
        "facts": facts or [fact()],
    }


def collect(session, facts=None, request="test"):
    return session.call("collect_pdk_parameter_facts", args(session, facts, request))


def test_scaled_domain_preserves_policy_and_report_survives_restart(tmp_path):
    session, bridge, _ = fixture(tmp_path)
    before = select(tmp_path, "synthetic", {})
    cdf = before.document("cell0/cdf.json")
    request = args(session)
    result = session.call("collect_pdk_parameter_facts", request)
    assert result["status"] == "ok", result
    assert result["collected_fields"] == 1 and result["live_captures"] == 1
    after = select(tmp_path, "synthetic", {})
    changed = after.document("cell0/cdf.json")
    assert changed["parameters"]["w"]["domain"] == {"kind": "interval", "min": 0.1, "max": 0.5}
    for name, row in cdf["parameters"].items():
        assert {k: v for k, v in row.items() if k != "domain"} == {
            k: v for k, v in changed["parameters"][name].items() if k != "domain"
        }
    for name in ("device.json", "cell0/simulation.json", "cell0/symbol.json"):
        assert before.available(name) == after.available(name)
        if before.available(name):
            assert before.document(name) == after.document(name)
    count = len(bridge.calls)
    restarted = PdkSession(
        bridge=bridge, workspace=tmp_path, environment={"SICO_PDK_WORKFLOW": "generation"}
    )
    assert restarted.call("collect_pdk_parameter_facts", request) == result
    assert len(bridge.calls) == count
    page = restarted.call("get_pdk_fact_report", {"report_ref": result["report_ref"]})
    assert page["items"][0]["normalized"]["min"] == 0.1
    assert page["items"][0]["source_identity"]["state"] == "verified"
    listing = restarted.call("get_pdk_fact_report", {"library": "synthetic"})
    assert listing["items"][0]["report_ref"] == result["report_ref"]
    assert max(p.stat().st_size for p in after.overlay.rglob("*.json")) <= LIMIT
    with pytest.raises(PdkUnavailable, match="different contents"):
        restarted.call(
            "collect_pdk_parameter_facts", {**request, "facts": [fact(reason="changed")]}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"complete": False},
        {"grid_status": "unknown"},
        {"value": {"kind": "interval", "min": 1}},
        {"value": {"kind": "unbounded", "reason": "No bounds were found"}},
        {"basis": "model_characterization"},
        {"basis": "pcell_limit"},
        {"input_unit": "V"},
        {"value": {"kind": "set"}},
        {"parameter": "p1"},
        {"field": "unit", "value": "V", "basis": "definition"},
        {"value": {"kind": "interval", "min": 400000, "max": 500000}},
        {"expected": {"kind": "interval", "min": 0}},
    ],
)
def test_bad_facts_never_change_index(tmp_path, change):
    session, _, _ = fixture(tmp_path)
    index = tmp_path / ".sico/ai/pdk-data/index.json"
    before = index.read_bytes()
    result = collect(session, [fact(**change)])
    assert result["status"] == "conflict", result
    assert result["error"]["fact_id"] == fact(**change)["id"]
    assert index.read_bytes() == before
    report = session.call("get_pdk_fact_report", {"report_ref": result["report_ref"]})
    assert report["result"]["status"] == "conflict"


@pytest.mark.parametrize("failure", ["document", "cdf", "dirty"])
def test_source_changes_keep_evidence_without_commit(tmp_path, failure):
    session, bridge, manual = fixture(tmp_path)
    index = tmp_path / ".sico/ai/pdk-data/index.json"
    before = index.read_bytes()
    if failure == "document":
        manual.write_text("changed manual")
    elif failure == "cdf":
        bridge.data["cdf"]["parameters"]["items"][0]["defValue"] = raw("99")
    else:
        bridge.data["database"]["master_state"]["modified"] = True
    result = collect(session)
    assert result["status"] == "conflict"
    assert result["error"]["code"] == "pdk_source_changed"
    assert index.read_bytes() == before


def test_context_unresolved_pagination_and_auxiliary_scope(tmp_path):
    session, _, _ = fixture(tmp_path)
    revision = args(session)["revision"]
    records = [
        fact(id="case-" + str(i), disposition="context", basis="model_characterization")
        for i in range(12)
    ]
    pending = fact(
        id="open",
        disposition="unresolved",
        basis="incomplete",
        reason="Upper bound and hard grid unavailable",
    )
    pending.pop("value")
    records.append(pending)
    result = collect(session, records)
    assert result["status"] == "incomplete" and result["revision"] == revision
    assert result["collected_fields"] == 0 and result["context_records"] == 12
    query = {"report_ref": result["report_ref"], "page_size": 2}
    found = []
    while True:
        page = session.call("get_pdk_fact_report", query)
        assert len(encode(page)) <= LIMIT
        found += page["items"]
        if page["complete"]:
            break
        query["cursor"] = page["next_cursor"]
    assert len(found) == 13
    root = session.standard.fact_collection.reports.path(result["report_ref"])
    assert max(p.stat().st_size for p in root.rglob("*.json") if "captures" not in p.parts) <= LIMIT
    assert collect(session, [fact(parameter="p1")], "aux")["status"] == "conflict"


def test_known_conflict_requires_exact_expectation(tmp_path):
    session, _, _ = fixture(tmp_path)
    assert collect(session)["status"] == "ok"
    old = {"kind": "interval", "min": 0.1, "max": 0.5}
    new = fact(value={"kind": "interval", "min": 200000, "max": 400000})
    assert collect(session, [new], "conflict")["status"] == "conflict"
    assert collect(session, [{**new, "expected": old}], "reviewed")["status"] == "ok"


@pytest.mark.parametrize("point", ["before_commit", "after_commit", "context_only"])
def test_interruption_has_frozen_report_and_safe_retry(tmp_path, monkeypatch, point):
    session, _, _ = fixture(tmp_path)
    reports = session.standard.fact_collection.reports
    request = args(
        session,
        [fact(disposition="context", basis="pcell_limit")] if point == "context_only" else None,
    )
    before = request["revision"]
    with monkeypatch.context() as patch:
        if point == "before_commit":
            from cadai.pdk_standard import overlay

            patch.setattr(
                overlay, "publish", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted"))
            )
        else:
            patch.setattr(
                reports, "finish", lambda *a: (_ for _ in ()).throw(OSError("interrupted"))
            )
        with pytest.raises(OSError, match="interrupted"):
            session.call("collect_pdk_parameter_facts", request)
    result = session.call("collect_pdk_parameter_facts", request)
    assert result["status"] == ("conflict" if point == "before_commit" else "ok")
    assert (result["revision"] == before) is (point != "after_commit")
    with pytest.raises(PdkUnavailable, match="frozen"):
        reports.save(result["report_ref"], {}, [])


def test_fact_report_digest_and_design_gate(tmp_path):
    session, bridge, _ = fixture(tmp_path)
    result = collect(session)
    root = session.standard.fact_collection.reports.path(result["report_ref"])
    path = root / "report.json"
    path.write_bytes(path.read_bytes().replace(b"Synthetic complete", b"Tampered complete"))
    with pytest.raises(PdkUnavailable, match="digest mismatch"):
        session.call("get_pdk_fact_report", {"report_ref": result["report_ref"]})
    design = PdkSession(bridge=bridge, workspace=tmp_path, environment={})
    with pytest.raises(PdkUnavailable, match="Normal design"):
        design.call("collect_pdk_parameter_facts", args(session))
