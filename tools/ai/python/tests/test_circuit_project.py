"""Project checks and crash boundaries without licensed tools."""

import inspect
import json
from copy import deepcopy

import pytest

from cadai.circuit_spec_schema import CircuitSpecError
from cadai.mcp import _TOOLS_BY_NAME, McpServer
from cadai.project_journal import OperationJournal
from cadai.project_preflight import check_path, check_run_path, preflight
from cadai.project_schema import OPERATIONS, operation_tool
from cadai.project_tools import call_project


class Client:
    def __init__(self):
        self.identity = {"ok": True, "session_id": "original", "pid": 100}
        self.receipt = None
        self.claim = None
        self.dispatches = 0
        self.codes = []

    def call(self, method, args):
        code = args["code"]
        self.codes.append(code)
        if code.startswith("aiProjectSession("):
            result = self.identity
        elif code.startswith("aiProjectClaim("):
            if self.claim:
                result = {"ok": False, "code": "already_claimed"}
            else:
                self.claim = code
                result = {"ok": True}
        elif code.startswith("aiProjectReceipt("):
            result = {
                "ok": True,
                "receipt_json": json.dumps(self.receipt) if self.receipt else None,
                "run_context_checked": True,
            }
        else:
            assert code.startswith('progn(aiProjectAssertClaim("original" ')
            self.dispatches += 1
            self.receipt = {"ok": True, "circuit_ref": "created:make", "saved": True}
            raise ConnectionError("reply lost after native write")
        return True, {"value": json.dumps(result)}


def execute(client, workspace, operation="create_circuit_from_plan", **overrides):
    arguments = {
        "prepare_ref": "create-prepare:prepare",
        "geometry_plan_digest": "a" * 64,
        "request_id": "make",
        **overrides,
    }
    return call_project(
        "execute_circuit_operation",
        {"operation": operation, "arguments": arguments},
        client,
        workspace,
        _TOOLS_BY_NAME,
    )


def status(client, workspace, refresh=True):
    return call_project(
        "get_circuit_operation",
        {"operation": "create_circuit_from_plan", "request_id": "make", "refresh": refresh},
        client,
        workspace,
        _TOOLS_BY_NAME,
    )


def test_lost_reply_restart_and_conflict(tmp_path):
    client = Client()
    first = execute(client, tmp_path)
    assert first["state"] == "retained_receipt" and first["receipt"]["saved"]
    assert "reply lost" in first["dispatch_diagnostic"]
    assert not first["automatic_resume_allowed"]
    assert execute(client, tmp_path)["receipt"] == first["receipt"]
    assert status(client, tmp_path)["receipt"] == first["receipt"]
    assert client.dispatches == 1
    with pytest.raises(CircuitSpecError, match="conflict"):
        execute(client, tmp_path, geometry_plan_digest="b" * 64)
    offline_calls = len(client.codes)
    assert status(client, tmp_path, False)["state"] == "offline_record"
    assert status(client, tmp_path, False)["last_observation"]["receipt"]["saved"]
    assert len(client.codes) == offline_calls
    client.identity = {**client.identity, "session_id": "replacement"}
    assert execute(client, tmp_path)["state"] == "session_mismatch"
    assert status(client, tmp_path, False)["last_observation"]["receipt"]["saved"]
    assert client.dispatches == 1


def test_unknown_and_partial_do_not_retry(tmp_path):
    client = Client()
    execute(client, tmp_path)
    client.receipt = None
    assert execute(client, tmp_path)["state"] == "outcome_unknown"
    client.receipt = {
        "ok": False,
        "circuit_ref": "created:make",
        "stage": "save",
        "saved": False,
        "error": "injected",
    }
    assert status(client, tmp_path)["state"] == "partial_or_failed"
    assert execute(client, tmp_path)["state"] == "partial_or_failed"
    assert client.dispatches == 1


def test_disk_failure_prevents_dispatch(tmp_path, monkeypatch):
    client = Client()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(OperationJournal, "write", fail)
    with pytest.raises(OSError, match="disk full"):
        execute(client, tmp_path)
    assert client.dispatches == 0 and client.claim is None


def test_failed_receipt_keeps_failure_state_with_unsaved_target(tmp_path):
    class ModifiedClient(Client):
        def call(self, method, args):
            ok, reply = super().call(method, args)
            if args["code"].startswith("aiProjectReceipt("):
                detail = json.loads(reply["value"])
                detail["target_observation"] = [["Work", "LDO", "schematic"], True,
                                                "schematic", True, True, True, True]
                reply["value"] = json.dumps(detail)
            return ok, reply

    client = ModifiedClient()
    execute(client, tmp_path)
    client.receipt = dict(ok=False, circuit_ref="created:make", target=["Work", "LDO", "schematic"],
                          stage="instances", saved=False, error="CDF callback changed requested value")
    result = status(client, tmp_path)
    assert result["state"] == "partial_or_failed" and result["target_modified"]
    assert result["receipt"] == client.receipt and not result["automatic_resume_allowed"]
    assert execute(client, tmp_path)["receipt"] == client.receipt and client.dispatches == 1


def test_claim_blocks_other_workspace(tmp_path):
    client = Client()
    execute(client, tmp_path / "one")
    execute(client, tmp_path / "two")
    assert client.dispatches == 1


def test_post_dispatch_disk_failure_retains_native_evidence(tmp_path, monkeypatch):
    original = OperationJournal.write
    count = []

    def fail_after_intent(self, record):
        count.append(record["state"])
        if len(count) >= 3:
            raise OSError("disk full after dispatch")
        return original(self, record)

    monkeypatch.setattr(OperationJournal, "write", fail_after_intent)
    client = Client()
    result = execute(client, tmp_path)
    assert result["receipt"]["saved"] and result["observation_persisted"] is False
    assert execute(client, tmp_path)["receipt"]["saved"]
    assert client.dispatches == 1


def test_dispatch_guard_and_unknown_claim_boundary(tmp_path):
    class ChangedClient(Client):
        def call(self, method, args):
            if args["code"].startswith("aiProjectClaim("):
                self.identity = {**self.identity, "session_id": "changed"}
                raise ConnectionError("claim reply lost")
            return super().call(method, args)

    client = ChangedClient()
    assert execute(client, tmp_path)["state"] == "session_mismatch"
    assert execute(client, tmp_path)["state"] == "session_mismatch"
    assert client.dispatches == 0


def test_locks_corrupt_records_and_schema(tmp_path):
    journal = OperationJournal(tmp_path)
    key = journal.key("create_circuit_from_plan", "make")
    with journal.lock(key):
        with pytest.raises(CircuitSpecError, match="in progress"):
            with journal.lock(key):
                pass
    journal.path(key).write_text('{"schema":"wrong"}')
    with pytest.raises(CircuitSpecError, match="invalid"):
        journal.read(key)
    with pytest.raises(CircuitSpecError):
        execute(Client(), tmp_path, request_id="../../escape")
    with pytest.raises(CircuitSpecError):
        call_project(
            "execute_circuit_operation",
            {"operation": "load_skill_file", "arguments": {}},
            Client(),
            tmp_path,
            _TOOLS_BY_NAME,
        )


def test_preflight_paths_and_units(tmp_path):
    model = tmp_path / "models.scs"
    model.write_text("simulator lang=spectre\n")
    assert len(check_path(str(model), "model")["sha256"]) == 64
    assert check_path(str(tmp_path), "directory")["status"] == "pass"
    assert check_path(str(tmp_path), "directory", 10**30)["status"] == "fail"
    assert check_path(str(model), "directory")["status"] == "fail"
    assert check_path(str(model), "executable")["status"] == "fail"
    assert check_path(str(tmp_path / "absent"), "model")["status"] == "fail"
    absent_run = tmp_path / "runs" / "top"
    run_row = check_run_path(str(absent_run), 0, 0)
    assert run_row["status"] == "pass" and run_row["missing"] is True
    assert run_row["created_by"] == "ade"
    assert run_row["checked_ancestor"] == str(tmp_path)
    assert "missing" not in check_run_path(str(tmp_path), 0, 0)
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
        "model_files": [str(model)],
    }
    observed = {
        "gui_available": True,
        "missing_apis": [],
        "libraries": [["Work", str(tmp_path)]],
        "targets": [[["Work", "Dut", "schematic"], False, None, False, True, False, False]],
    }
    good = preflight(args, {}, observed)
    assert good["checks_passed"] and not good["simulation_qualified"]
    missing_run = deepcopy(args)
    missing_run["directories"] = [
        {
            "path": str(absent_run),
            "purpose": "run",
            "min_free_bytes": 0,
            "min_free_inodes": 0,
        }
    ]
    assert preflight(missing_run, {}, observed)["checks_passed"]
    missing_project = deepcopy(missing_run)
    missing_project["directories"][0]["purpose"] = "project"
    assert not preflight(missing_project, {}, observed)["checks_passed"]
    for change in ("modified", "exists", "library", "gui", "api"):
        bad = deepcopy(observed)
        if change == "modified":
            args["targets"][0]["intent"] = "read"
            bad["targets"][0][1:4] = [True, "schematic", True]
            bad["targets"][0][6] = True
        elif change == "exists":
            args["targets"][0]["intent"] = "create"
            bad["targets"][0][1] = True
        elif change == "library":
            bad["libraries"] = [["Work", "/different"]]
        elif change == "gui":
            bad["gui_available"] = False
        else:
            bad["missing_apis"] = ["missing"]
        assert not preflight(args, {}, bad)["checks_passed"]


def test_registration_profile_and_real_nested_schemas(tmp_path):
    for name in OPERATIONS:
        assert operation_tool(name) in _TOOLS_BY_NAME
    client = Client()
    server = McpServer(client, workspace=tmp_path)
    response = server.handle(
        {
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "execute_circuit_operation",
                "arguments": {
                    "operation": "create_simulation_setup",
                    "arguments": {"request_id": "bad"},
                },
            },
        }
    )
    assert response["error"]["code"] == -32602
    assert client.codes == []
    if "profile" in inspect.signature(McpServer).parameters:
        verification = McpServer(client, workspace=tmp_path, profile="verification")
        listed = verification.handle({"id": 2, "method": "tools/list"})
        assert "execute_circuit_operation" not in {
            tool["name"] for tool in listed["result"]["tools"]
        }


def test_all_dispatch_routes_use_claimed_backend(tmp_path):
    from cadai.project_tools import GuardedClient, dispatch

    client = Client()
    record = {"session": client.identity, "key": "config:config", "input_digest": "a" * 64}
    guarded = GuardedClient(client, record)
    args = {
        "library": "Work",
        "cell": "Dut",
        "top_library": "Work",
        "top_cell": "Dut",
        "task_ref": "task:work",
        "request_id": "config",
    }
    with pytest.raises(ConnectionError):
        dispatch("create_circuit_config", args, guarded, tmp_path, None)
    assert "aiConfigCreate(" in client.codes[-1]
    assert 'let(((aiProjectDispatchKey "config:config")' in client.codes[-1]
