"""Evidence checks for model-selected references; semantic review remains explicit."""

import json
import re

from knowledge_model_harness import final_text, knowledge_calls, tool_value

QUESTIONS = (
    (
        "Streamable HTTP bearer_token_env_var",
        "Streamable HTTP 的 bearer_token_env_var 应如何配置？",
        ("extend_mcp", "extend/mcp", "config-reference"),
    ),
    (
        "skills/extraRoots/set",
        "app-server 的 skills/extraRoots/set 有什么用途、路径和作用域要求？",
        ("app-server",),
    ),
    (
        "SKILL.md name description",
        "Codex Skills 的 SKILL.md 中 name 和 description 有哪些要求？与 Cadence SKILL 有什么区别？",
        ("build-skills", "skills"),
    ),
    (
        "sandbox_mode approval_policy",
        "sandbox_mode 与 approval_policy 分别控制什么，如何配合使用？",
        ("config", "security", "sandbox"),
    ),
)

CITATIONS = (
    "请根据已选的本地 OpenAI 文档和 OpenAI 官方文档 MCP 解释，读取相关原文后给出简短中文回答。"
    "分别引用两类来源，引用中保留返回的完整 URI、location 起止行和完整 version。"
    "如果证据不足、过期或某来源失败，说明限制。只核对文档，不创建目标、不执行代码或委派任务。"
)


def cited(text, value):
    """Require the URI, version and location in the same citation block."""
    uri = value["uri"].split("#", 1)[0]
    location = value["location"]
    pattern = (
        r"(?<!\d)" + str(location["start"]) + r"\s*[-–—]\s*L?" + str(location["end"]) + r"(?!\d)"
    )
    pattern += (
        r"|\bstart\s*[:=]?\s*"
        + str(location["start"])
        + r"\s*,\s*end\s*[:=]?\s*"
        + str(location["end"])
        + r"\b"
    )
    blocks = re.split(r"\n\s*\n|\n(?=\|)|\n(?=\s*[-*] (?:URI[:：]|[^\n]*(?:https?|file)://))", text)
    return any(
        (uri in block or uri.startswith("file://") and uri[7:] in block)
        and value["version"] in block
        and re.search(pattern, block)
        for block in blocks
    )


def check_question(result, expected):
    calls, text = knowledge_calls(result), final_text(result)
    seen = {(h["source_id"], h["evidence_id"]): h for h in result.get("prior_search_hits", [])}
    reads, sizes = [], []
    expected_sources = set()
    for call in calls:
        if not call.get("result"):
            continue
        value = tool_value(call)
        sizes.append(len(json.dumps(value, ensure_ascii=False).encode()))
        if call["tool"] == "search":
            seen.update({(h["source_id"], h["evidence_id"]): h for h in value.get("hits", [])})
            expected_sources.update(
                h["source_id"]
                for h in value.get("hits", [])
                if h.get("source_rank", 0) <= 3 and any(part in h["uri"] for part in expected)
            )
        elif call["tool"] == "read" and value.get("verified"):
            hit = seen.get((value["source_id"], value["evidence_id"]), {})
            location = value["location"]
            uri = value["uri"].split("#", 1)[0]
            reads.append(
                {
                    "source_id": value["source_id"],
                    "uri": value["uri"],
                    "location": location,
                    "version": value["version"],
                    "from_search": hit.get("version") == value["version"],
                    "cited": bool(cited(text, value)),
                    "expected_document": any(part in uri for part in expected),
                }
            )
    metrics = [
        m
        for m in result.get("resources", {}).get("mcp_metrics", [])
        if m["name"] == "sico_knowledge"
    ]
    sources = {r["source_id"] for r in reads if r["from_search"] and r["cited"]}
    checks = {
        "completed": result["status"] == "completed",
        "both_sources_cited": sources == {"local-openai", "openai-docs"},
        "expected_documents_retrieved": expected_sources == {"local-openai", "openai-docs"},
        "bounded_calls": len(calls) <= 12 and not result.get("budget_exceeded"),
        "bounded_output": bool(sizes) and max(sizes) <= 32768,
        "small_inventory": len(metrics) == 1
        and metrics[0]["count"] == 2
        and metrics[0]["array_compact_bytes"] < 2500,
    }
    usage = [event["payload"] for event in result["events"] if event["kind"] == "token.usage"]
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "references": reads,
        "calls": len(calls) if "items" in result else None,
        "trace_available": "items" in result,
        "approvals": result.get("approval_count"),
        "searches": sum(c["tool"] == "search" for c in calls),
        "max_output_bytes": max(sizes, default=0),
        "inventory": metrics,
        "elapsed_seconds": result["elapsed_seconds"],
        "usage": usage[-1] if usage else {},
    }
