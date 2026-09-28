"""Validated Codex execution scope and its version-pinned RPC projection."""

from copy import deepcopy

MODES = (("只读", "read-only"), ("工作区写入", "workspace-write"),
         ("完全访问", "danger-full-access"))
SCOPE_NOTE = ("仅控制 Codex 本地命令与文件操作；Virtuoso、MCP、浏览器及网页搜索使用各自权限。"
              "修改随下一次提交生效，排队任务保留提交时的选择。")


def default_access(*, legacy=False):
    return {"sandbox": "danger-full-access" if legacy else "workspace-write",
            "approval": "never" if legacy else "on-request", "network": legacy}


def validate(value):
    if (not isinstance(value, dict) or set(value) != {"sandbox", "approval", "network"}
            or not isinstance(value["sandbox"], str)
            or value["sandbox"] not in {mode for _, mode in MODES}
            or not isinstance(value["approval"], str)
            or value["approval"] not in {"on-request", "never"}
            or type(value["network"]) is not bool):
        raise ValueError("Invalid Codex execution permissions")
    result = deepcopy(value)
    # Full access cannot promise to block command networking.
    if result["sandbox"] == "danger-full-access":
        result["network"] = True
    return result


def approval_policy(value, interactive):
    ask = interactive and value["approval"] == "on-request"
    return {"granular": {"mcp_elicitations": interactive, "sandbox_approval": ask,
                        "rules": ask, "request_permissions": ask, "skill_approval": ask}}


def turn_params(value, *, interactive):
    value = validate(value)
    policy = {"type": {"read-only": "readOnly", "workspace-write": "workspaceWrite",
                       "danger-full-access": "dangerFullAccess"}[value["sandbox"]]}
    if value["sandbox"] != "danger-full-access":
        policy["networkAccess"] = value["network"]
    if value["sandbox"] == "workspace-write":
        policy.update(writableRoots=[], excludeSlashTmp=False, excludeTmpdirEnvVar=False)
    return {"sandboxPolicy": policy, "approvalPolicy": approval_policy(value, interactive),
            "approvalsReviewer": "user"}


def configure_thread(rpc, thread_id, value, *, interactive):
    """Restore the exact acknowledged scope, including read-only networking."""
    rpc.request("thread/settings/update", {"threadId": thread_id,
                                          **turn_params(value, interactive=interactive)})
