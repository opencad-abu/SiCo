"""Untrusted/incomplete observations must never authorize writes or replay."""

import json
from copy import deepcopy

import pytest
from test_circuit_project import Client, execute, status

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.project_journal import OperationJournal
from cadai.project_preflight import preflight
from cadai.project_tools import report


def observations(tmp_path):
    args = {
        "presentation": "foreground",
        "targets": [
            {
                "library": "Work",
                "cell": "Dut",
                "view": "schematic",
                "oa_view_type": "schematic",
                "intent": "create",
            }
        ],
        "libraries": [{"library": "Work", "expected_path": str(tmp_path)}],
        "directories": [
            {"path": str(tmp_path), "purpose": "run", "min_free_bytes": 0, "min_free_inodes": 0}
        ],
        "model_files": [],
    }
    observed = {
        "gui_available": True,
        "missing_apis": None,
        "libraries": [["Work", str(tmp_path)]],
        "targets": [[["Work", "Dut", "schematic"], False, None, False, True, False, False]],
    }
    return args, observed


def rejected_preflight(args, observed):
    try:
        result = preflight(args, {}, observed)
    except CircuitSpecError:
        return
    assert not result["checks_passed"]


@pytest.mark.parametrize("position", [0, 1, 2])
def test_preflight_checks_exact_target_identity(tmp_path, position):
    args, observed = observations(tmp_path)
    observed["targets"][0][0][position] = "Other"
    rejected_preflight(args, observed)


@pytest.mark.parametrize("position", [1, 3, 4, 5, 6])
@pytest.mark.parametrize("unknown", [None, 0, "false"])
def test_preflight_requires_actual_boolean_observations(tmp_path, position, unknown):
    args, observed = observations(tmp_path)
    observed["targets"][0][position] = unknown
    rejected_preflight(args, observed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("targets", None),
        ("targets", [[]]),
        ("libraries", [["Work"]]),
        ("libraries", [["Work", None], ["Work", "/ambiguous"]]),
        ("gui_available", "true"),
        ("missing_apis", False),
    ],
)
def test_preflight_rejects_malformed_observation(tmp_path, field, value):
    args, observed = observations(tmp_path)
    observed[field] = value
    rejected_preflight(args, observed)


def test_native_nil_empty_api_list_remains_supported(tmp_path):
    args, observed = observations(tmp_path)
    assert preflight(args, {}, observed)["checks_passed"]


@pytest.mark.parametrize(
    "value", [None, [], True, {}, {"ok": True}, {"ok": True, "session_id": "s", "pid": False}]
)
def test_invalid_identity_never_claims_or_dispatches(tmp_path, value):
    client = Client()
    client.identity = value
    with pytest.raises(CircuitSpecError):
        execute(client, tmp_path)
    assert client.claim is None and client.dispatches == 0


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        True,
        {},
        {"ok": True},
        {"ok": True, "saved": True, "circuit_ref": "created:other"},
        {"ok": "true", "saved": True, "circuit_ref": "created:make"},
        {"ok": True, "saved": None, "circuit_ref": "created:make"},
    ],
)
def test_bad_retained_receipt_is_unknown_and_never_replayed(tmp_path, value):
    client = Client()
    execute(client, tmp_path)
    original = client.call

    def call(method, args):
        if args["code"].startswith("aiProjectReceipt("):
            return True, {"value": json.dumps({"ok": True, "receipt_json": json.dumps(value)})}
        return original(method, args)

    client.call = call
    for result in (status(client, tmp_path), execute(client, tmp_path)):
        assert result["state"] == "outcome_unknown" and result["ok"] is False
        assert result["automatic_resume_allowed"] is False
    assert client.dispatches == 1
    assert status(client, tmp_path, False)["last_observation"]["receipt"]["saved"]


@pytest.mark.parametrize("value", [None, [], {}, {"schema": "cad.circuit.operation.v1"}])
def test_corrupt_journal_has_structured_failure(tmp_path, value):
    journal = OperationJournal(tmp_path)
    key = journal.key("create_circuit_from_plan", "make")
    with journal.lock(key):
        journal.path(key).write_text(json.dumps(value))
    with pytest.raises(CircuitSpecError):
        status(Client(), tmp_path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "completed"),
        ("operation", "draw_symbol"),
        ("request_id", "other"),
        ("session", {}),
        ("input_digest", "unknown"),
        ("observation", []),
    ],
)
def test_journal_identity_and_required_fields_validated(tmp_path, field, value):
    client = Client()
    execute(client, tmp_path)
    journal = OperationJournal(tmp_path)
    key = journal.key("create_circuit_from_plan", "make")
    record = deepcopy(journal.read(key))
    record[field] = value
    journal.path(key).write_text(json.dumps(record))
    with pytest.raises(CircuitSpecError):
        status(client, tmp_path)
    assert client.dispatches == 1


@pytest.mark.parametrize("context", [True, False, None, "true"])
def test_run_history_and_session_are_separate_evidence(context):
    client = Client()
    record = {
        "operation": "run_simulation_setup",
        "request_id": "run",
        "key": "simulation-run:run",
        "input_digest": "a" * 64,
        "session": client.identity,
        "created_at": "recorded",
        "state": "dispatching",
        "input_summary": {"setup_ref": "simulation:setup"},
    }
    retained = {
        "ok": True,
        "run_ref": "simulation-run:run",
        "setup_ref": "simulation:setup",
        "state": "completed",
        "history": "Interactive.7",
    }
    original = client.call

    def call(method, args):
        if args["code"].startswith("aiProjectReceipt("):
            return True, {
                "value": json.dumps(
                    {
                        "ok": True,
                        "receipt_json": json.dumps(retained),
                        "run_context_checked": context,
                        "target_observation": [
                            ["Work", "Setup", "maestro"],
                            True,
                            "maestro",
                            True,
                            True,
                            True,
                            False,
                        ],
                        "automatic_resume_allowed": True,
                        "live_integrity_verified": True,
                    }
                )
            }
        return original(method, args)

    client.call = call
    result = report(record, client)
    assert result["state"] == ("completed" if context is True else "context_lost")
    assert result["receipt"]["history"] == "Interactive.7"
    assert not result["automatic_resume_allowed"] and not result["live_integrity_verified"]
    retained["history"] = None
    assert report(record, client)["state"] == "outcome_unknown"
