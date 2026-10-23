"""Review direct deletion or reviewed-write calls in the raw SKILL escape hatch.

The normal circuit edit path is reviewed by ``prepare_circuit_edit`` and gated by
the host.  ``eval_skill`` and ``load_skill_file`` can otherwise call the same
native deletion APIs directly, so this module turns those calls into an explicit
review request before dispatch.  Detection is deliberately conservative: dynamic
strings and native write entry points are included even when the source does not
use a literal ``delete`` identifier.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .circuit_spec_schema import digest
from .skill_check_tools import read_skill_file
from .skill_lexer import tokens

APPROVAL = "执行这次删除"
REJECTION = "取消"
MAX_PREVIEW = 2000

DELETE_PREFIXES = (
    "dbdelete",
    "dddelete",
    "schdelete",
    "gedelete",
    "ledelete",
    "deletefile",
)
REVIEWED_NATIVE_WRITES = frozenset(
    {
        "aicreateexecute",
        "aicdfupdateapply",
        "aidrawsymbol",
        "aitemplatesymbolcreate",
    }
)


def _findings(source: str) -> tuple[list[str], list[str]]:
    """Return literal deletion/write calls and dynamic-call sites."""
    calls, dynamic = set(), set()
    for token in tokens(source):
        if token.kind == "name":
            name = token.value.lower()
            if name in REVIEWED_NATIVE_WRITES or name.startswith(DELETE_PREFIXES):
                calls.add(token.value)
            if name in {"eval", "funcall", "apply", "getd", "load", "system"}:
                dynamic.add(token.value)
        elif token.kind == "string":
            text = source[token.start:token.end].lower()
            if any(prefix in text for prefix in DELETE_PREFIXES) or any(
                    name in text for name in REVIEWED_NATIVE_WRITES
            ):
                calls.add("dynamic deletion text")
    if dynamic and not calls:
        # Dynamic dispatch can resolve a deletion API at runtime.  The source
        # alone cannot prove it is safe, so it needs the same user review.
        calls.add("dynamic SKILL dispatch")
    return sorted(calls, key=str.lower), sorted(dynamic, key=str.lower)


def prepare(tool: str, arguments: dict, workspace: Path) -> dict | None:
    """Build a deletion review for one raw SKILL call, or ``None`` when safe."""
    if tool == "eval_skill":
        source = arguments.get("code")
        if not isinstance(source, str):
            return None
        raw = source.encode("utf-8")
        target = "inline SKILL"
    elif tool == "load_skill_file":
        requested = arguments.get("path")
        if not isinstance(requested, str):
            return None
        try:
            path, raw = read_skill_file(requested, Path(workspace))
        except Exception:
            # The normal handler reports unreadable or invalid files.
            return None
        target = str(path)
        try:
            source = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:
        return None

    calls, dynamic = _findings(source)
    if not calls:
        return None
    body = {
        "tool": tool,
        "target": target,
        "calls": calls,
        "dynamic_calls": dynamic,
        "source_bytes": len(raw),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_preview": source[:MAX_PREVIEW],
    }
    review_digest = digest(body)
    shown = "、".join(calls[:12])
    if len(calls) > 12:
        shown += " 等"
    return {
        "review_required": True,
        "review_kind": "skill_deletion",
        "review_digest": review_digest,
        "approval_label": APPROVAL,
        "rejection_label": REJECTION,
        "next_action": "ask_user_to_approve_skill_deletion",
        "selection_question": {
            "id": "skill_deletion",
            "header": "删除审阅",
            "question": (
                f"{tool} 请求在 Virtuoso 中执行删除或受控写入（{shown}），"
                f"目标为 {target}。只有用户批准后才会派发；是否执行？"
            ),
            "options": [
                {
                    "label": APPROVAL,
                    "description": "按本次审阅的精确代码或文件内容执行一次。",
                },
                {
                    "label": REJECTION,
                    "description": "不执行任何 SKILL，保留当前设计。",
                },
            ],
        },
        "review": body,
    }


__all__ = ["APPROVAL", "REJECTION", "prepare"]
