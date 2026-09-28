"""Service-owned record metadata commands, serialized with runtime creation and retirement."""

from ..core.contracts import RunState, identifier
from ..core.tools import ToolRegistry
from ..providers.config import provider_from_config
from ..storage.journal import SessionJournal
from ..storage.names import MAX_NAME_CHARS, compact_name
from ..transport.framing import ProtocolError
from .backend import create_backend
from .recovery_facts import read_facts
from .recovery_environment import recovery_environment
from .record_deletion import require_review
from .damaged_record import delete_remnant, inspect_record
from .service_protocol import exact_fields


def record_command(hub, wire, params):
    fields = {"session_id", "runtime_id", "action", "name"}
    exact_fields(params, fields | ({"review_version"} if "review_version" in params else set()))
    reviewed = params.get("review_version")
    if "review_version" in params:
        from .recovery_contract import version

        version(reviewed)
        if params["action"] != "delete":
            raise ProtocolError("Only deletion accepts a review")
    session_id = identifier(params["session_id"])
    runtime = params["runtime_id"]
    if runtime is not None:
        identifier(runtime)
    if not isinstance(params["action"], str) or params["action"] not in {"delete", "rename"}:
        raise ProtocolError("Invalid record action")
    if params["action"] == "delete":
        if params["name"] is not None:
            raise ProtocolError("Deletion has no name")
    else:
        value = params["name"]
        if (not isinstance(value, str) or not value.strip() or "\0" in value
                or len(value.strip()) > MAX_NAME_CHARS):
            raise ValueError("会话名称不能为空或过长")
    with hub.owner._mutation:
        if session_id in hub.owner.controllers:
            raise ValueError("活动会话必须先结束并归档，收尾完成后才能操作记录")
        facts, damaged = inspect_record(hub.owner.project, session_id)
        if damaged is not None:
            if params["action"] != "delete":
                raise ValueError("会话内容不完整，仅可删除残留记录")
            delete_remnant(hub, wire, session_id, reviewed)
            hub.recovery.retired(session_id)
            hub.workspace.catalog.request().result()
            return None
        if facts.snapshot and facts.snapshot["project_id"] != hub.descriptor.project_id:
            raise ProtocolError("Record belongs to another project")
        if facts.deleted:
            if params["action"] != "delete":
                raise ValueError("此记录已删除")
        else:
            if facts.unresolved and reviewed is None:
                raise ValueError("会话仍有未决工作，请先核对原操作")
            if runtime is not None:
                if not facts.ends or facts.ends[-1]["payload"]["address"]["runtime_id"] != runtime:
                    raise ValueError("记录运行实例已变化，请刷新后重试")
            _write_record(hub, wire, params, facts, reviewed)
        # A committed tombstone is final, but a previous attempt may have lost
        # its presentation updates. Retry those without issuing another native RPC.
        if params["action"] == "delete":
            hub.recovery.retired(session_id)
        hub.workspace.catalog.request().result()
        return None


def _write_record(hub, wire, params, facts, reviewed):
    session_id = params["session_id"]
    with SessionJournal(hub.owner.project, session_id) as journal:
        # The writer lock also excludes legacy processes outside this service.
        locked = read_facts(hub.owner.project, session_id)
        if locked.version != facts.version or reviewed is not None and locked.version != reviewed:
            raise ValueError("记录已变化，请刷新后重新核对")
        if wire.connection.closed.is_set():
            raise ValueError("记录操作连接已断开")
        if locked.unresolved:
            require_review(hub, session_id, locked, journal.events(), reviewed)
            journal.append("codex.thread.deleted", dict(
                source="user_review", review_version=reviewed,
                abandoned_inputs=[row["input_id"] for row in locked.inputs],
                original_end=locked.ends[-1]["payload"] if locked.ends else None,
                external_outcome="unconfirmed"), journal.state or RunState(locked.context))
        else:
            _mutate(hub.owner, journal, locked, params)


def _mutate(owner, journal, facts, params):
    threads = [row["payload"].get("thread_id") for row in journal.events()
               if row["kind"] == "codex.thread"]
    if not threads:
        kind = "codex.thread.deleted" if params["action"] == "delete" else "codex.thread.name"
        payload = {} if params["action"] == "delete" else dict(
            name=compact_name(params["name"]), source="user")
        journal.append(kind, payload, journal.state or RunState(facts.context))
        return
    if any(not isinstance(thread, str) or not thread.strip() for thread in threads):
        raise ValueError("记录缺少有效的原 Codex 线程标识，不能操作记录")
    snapshot = facts.snapshot
    if snapshot is None:
        raise ValueError("记录缺少原模型配置，不能操作原 Codex 线程")
    environment = recovery_environment(owner, journal.session_id, snapshot)
    config = snapshot["provider_config"]
    provider = provider_from_config(config, environment=environment)
    loop = create_backend(provider, ToolRegistry(), journal, facts.context, workbench=False)
    try:
        if params["action"] == "delete":
            loop.delete_thread()
        else:
            loop.set_thread_name(params["name"])
    finally:
        loop.close()
