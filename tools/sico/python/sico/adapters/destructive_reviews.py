"""One admission gate for destructive tool calls that need host approval."""

from cadai.destructive_skill import prepare as prepare_skill_review

from ..core.contracts import ToolResult


def _status(registry, kind, review_digest, context):
    validator = getattr(registry, "destructive_review_validator", None)
    return validator(kind, review_digest, context) if validator else "unavailable"


def gate(registry, kind, review_digest, context):
    """Return a blocking/error receipt when approval is absent, else ``None``."""
    if not isinstance(review_digest, str) or not review_digest:
        return ToolResult("approval_required", "删除审阅摘要缺失；未派发写入", data={
            "ok": False, "code": "delete_review_digest_required",
            "review_kind": kind, "operation_dispatched": False,
            "automatic_retry": False,
        })
    status = _status(registry, kind, review_digest, context)
    if status == "approved":
        return None
    codes = {
        "pending": ("waiting_user", "delete_review_pending",
                    "删除审阅尚未答复；未派发写入"),
        "rejected": ("tool_error", "delete_review_rejected",
                     "用户拒绝了删除审阅；未派发写入"),
        "missing": ("approval_required", "delete_review_required",
                    "没有与该摘要匹配的用户审阅；未派发写入"),
        "unavailable": ("tool_error", "delete_review_unavailable",
                        "删除审阅服务不可用；未派发写入"),
    }
    result_status, code, message = codes.get(status, codes["unavailable"])
    return ToolResult(result_status, message, data={
        "ok": False, "code": code, "review_kind": kind,
        "review_digest": review_digest, "operation_dispatched": False,
        "automatic_retry": False,
    })


def circuit_gate(registry, name, arguments, context):
    """Gate the direct edit tool and its journalled operation alias."""
    if name == "execute_circuit_edit":
        digest = arguments.get("approval_digest")
    elif name == "execute_circuit_operation" and arguments.get("operation") == "edit_circuit":
        inner = arguments.get("arguments")
        inner = inner if isinstance(inner, dict) else {}
        approval = inner.get("approval")
        digest = inner.get("approval_digest")
        if digest is None and isinstance(approval, dict):
            digest = approval.get("review_digest")
    else:
        return None
    return gate(registry, "circuit_edit", digest, context)


def skill_gate(registry, name, arguments, context, workspace):
    """Review raw SKILL deletion and return the arguments to dispatch."""
    if name not in {"eval_skill", "load_skill_file"}:
        return None, arguments
    review = prepare_skill_review(name, arguments, workspace)
    if review is None:
        return None, {k: v for k, v in arguments.items() if k != "approval_digest"}
    status = _status(registry, "skill_deletion", review["review_digest"], context)
    if status == "approved":
        token = arguments.get("approval_digest")
        if token is not None and token != review["review_digest"]:
            return ToolResult("tool_error", "删除审阅摘要不匹配；SKILL 未执行", data={
                "ok": False, "code": "delete_review_mismatch",
                "review_digest": review["review_digest"],
            }), arguments
        return None, {k: v for k, v in arguments.items() if k != "approval_digest"}
    if status == "pending":
        return ToolResult("waiting_user", "删除审阅尚未答复；SKILL 未执行", data=review), arguments
    if status == "rejected":
        return ToolResult("tool_error", "用户拒绝了删除审阅；SKILL 未执行", data={
            "ok": False, "code": "delete_review_rejected",
            "review_digest": review["review_digest"],
        }), arguments
    if status == "missing":
        return ToolResult(
            "waiting_user",
            "这次 SKILL 包含删除或受控写入，需要先完成删除审阅；未执行",
            data=review,
        ), arguments
    return ToolResult(
        "approval_unavailable", "删除审阅服务不可用；SKILL 未执行",
        data={"ok": False, "code": "delete_review_unavailable"},
    ), arguments


__all__ = ["circuit_gate", "gate", "skill_gate"]
