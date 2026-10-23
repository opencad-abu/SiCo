"""Window-independent reconciliation using the same authenticated service commands."""

import json
from contextlib import ExitStack

from ..storage.history import SessionReader
from ..storage.roots import agent_root
from .reconcile_calls import from_events
from .recovery_facts import read_facts
from .remote_frontend import RemoteFrontend
from .service_startup import ServiceStartup


def inspect(project, session_id):
    facts = read_facts(project, session_id)
    task = facts.task.get("id", "")
    reader = SessionReader(agent_root(project), session_id)
    calls = from_events(reader.events(), task)
    return dict(session_id=session_id, task_id=task, status=facts.task.get("status", ""),
                operations=calls.rows(), next_step=(
                    "使用 --task <task_id> --decision query --call <id> 查询；"
                    "确认后选择 completed/not_executed 并提供 --evidence。"
                    "无未决调用时选择 no_calls；不确定时选择 uncertain。"))


def execute(api, session_id, task_id, action, call_id, evidence, timeout):
    view = api.recovery.inspect(session_id).result(timeout)
    if view["task_id"] != task_id:
        raise ValueError("原任务已变化，请重新运行 reconcile 查看待核对记录")
    token = (api.open_session(session_id).result(timeout) if view["state"] == "live"
             else api.recovery.continue_session(session_id).result(timeout))
    if action == "query":
        return api.command(token, "query_interrupted", call_id,
                           expected_task=task_id).result(timeout)
    api.control.ensure(token).result(timeout)
    return api.command(token, "resolve_interrupted", call_id, action,
                       expected_task=task_id, evidence=evidence).result(timeout)


def run(args):
    try:
        if args.decision == "inspect":
            result = inspect(args.project_dir, args.session)
        else:
            if not args.task:
                raise ValueError("请先查看记录，再提供原任务 --task；不会自动选择或重放任务")
            if args.decision in {"completed", "not_executed"} and not args.evidence.strip():
                raise ValueError("请提供 --evidence 核对依据，仍不确定时选择 uncertain")
            with ExitStack() as stack:
                startup = ServiceStartup(args.project_dir, timeout=args.timeout)
                stack.callback(startup.close)
                descriptor = startup.ready.result(args.timeout)
                api = RemoteFrontend(descriptor, args.project_dir, args.session)
                stack.callback(api.close_desktop)
                result = execute(api, args.session, args.task, args.decision,
                                 args.call, args.evidence, args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status="not_resolved", message=str(exc), next_step=(
            "重新运行 reconcile 查看原任务及核对记录；决定回执不明时先查看记录。"
            "其他窗口持有控制权时，请先在该窗口释放控制权；原写操作不会重放。")), ensure_ascii=False))
        return 1
