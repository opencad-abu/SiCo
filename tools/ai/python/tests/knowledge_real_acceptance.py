"""Opt-in AS5 acceptance; never collected by pytest, credentials only from environment.

Run in the source-test environment documented in docs/SICO_AGENT_SETTINGS_PLAN.md.
All output belongs in a new private directory outside version control.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path

from knowledge_fixtures import docs_server, local, manifest, remote
from knowledge_model_checks import CITATIONS, QUESTIONS, check_question
from knowledge_model_harness import (
    configure_document_session,
    final_text,
    knowledge_calls,
    run_turn,
    tool_value,
)
from knowledge_pdk_boundary import CLAIM, QUESTION, pdk_digest, pdk_registry
from sico.codex.settings import CodexSettings
from sico.knowledge.engine import Knowledge
from sico.service.backend import create_backend
from sico.storage.journal import SessionJournal
from test_agent_codex_native import CONTEXT, registry


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def provider(config, cli):
    environment = dict(os.environ)
    base = environment.get("SICO_API_BASE_URL") or environment.get("SICO_API_URL")
    return CodexSettings(
        {
            "backend": "codex",
            "provider": "responses",
            "base_url": base,
            "model": environment["SICO_MODEL"],
            "codex_executable": cli,
            "external_resources": str(config),
            "tool_format": "flat",
            "timeout": 30,
            "idle_timeout": 90,
        },
        environment,
    )


def turn(project, identity, settings, question, tools=None):
    with SessionJournal(project, identity) as journal:
        configure_document_session(journal, CONTEXT)
        backend = create_backend(settings, tools or registry([]), journal, CONTEXT)
        try:
            return run_turn(backend, question)
        finally:
            backend.close()


def project_settings(out, cli, sources):
    project = out / "project"
    project.mkdir(parents=True)
    path = project / "resources.json"
    save(path, manifest(*sources))
    settings = provider(path, cli)
    builds = [
        Knowledge(project, manifest(*sources), settings.environment).build(s["id"])
        for s in sources
        if s["type"] == "local"
    ]
    assert all(b["status"] == "ready" for b in builds), builds
    save(out / "builds.json", builds)
    return project, path, settings


def documents(out, cli, root):
    source = {
        **local(out / "unused"),
        "id": "local-openai",
        "name": "OpenAI local snapshot",
        "path": root,
        "formats": ["markdown"],
        "include": ["codex-docs/*.md"],
    }
    cloud = {
        **remote("https://developers.openai.com/mcp"),
        "id": "openai-docs",
        "name": "OpenAI Docs MCP",
        "collection": "codex",
        "timeout_sec": 45,
    }
    project, config, settings = project_settings(out, cli, [source, cloud])
    records = []
    first_thread = None
    for number, (query, question, expected) in enumerate(QUESTIONS, 1):
        result = turn(project, "docs-" + str(number), settings, question + CITATIONS)
        save(out / f"question-{number}.json", result)
        record = {"query": query, **check_question(result, expected)}
        records.append(record)
        if number == 1:
            first_thread = result["thread_id"]
        print(json.dumps({"question": number, **record}, ensure_ascii=False), flush=True)
    save(config, manifest())
    resumed = turn(
        project,
        "docs-1",
        settings,
        "继续核对 bearer_token_env_var：请重新检索并读取两类来源，确认引用当前仍有效。" + CITATIONS,
    )
    save(out / "resumed.json", resumed)
    resume = check_question(resumed, QUESTIONS[0][2])
    resume["same_thread"] = resumed["thread_id"] == first_thread
    resume["pending_defaults"] = (
        resumed.get("resources", {}).get("configuration", {}).get("pending") is True
    )
    resume["passed"] &= resume["same_thread"] and resume["pending_defaults"]
    fresh = turn(
        project,
        "fresh-defaults",
        settings,
        "当前新会话已配置哪些知识源？若未配置，请直接说明，不调用其他工具。",
    )
    save(out / "fresh.json", fresh)
    new = {
        "completed": fresh["status"] == "completed",
        "knowledge_not_called": not knowledge_calls(fresh),
        "knowledge_not_exposed": not any(
            m["name"] == "sico_knowledge" for m in fresh.get("resources", {}).get("mcp_metrics", [])
        ),
    }
    return {
        "questions": records,
        "resume": resume,
        "fresh": new,
        "passed": all(r["passed"] for r in records) and resume["passed"] and all(new.values()),
    }


def boundary(out, cli):
    source = local(out / "docs")
    (Path(source["path"]) / "claim.md").write_text(CLAIM)
    project, _config, settings = project_settings(out, cli, [source])
    tools = pdk_registry(project)
    before = pdk_digest(project)
    result = turn(project, "pdk-boundary", settings, QUESTION, tools)
    save(out / "result.json", result)
    checks = {
        "completed": result["status"] == "completed",
        "package_unchanged": before == pdk_digest(project),
        "read_reference": any(
            c["tool"] == "read" and tool_value(c).get("verified")
            for c in knowledge_calls(result)
            if c.get("result")
        ),
        "structured_read": any(
            e["kind"] == "tool.finished" and e["payload"].get("name") == "get_pdk_data"
            for e in result["events"]
        ),
        "no_pdk_write_attempt": not any(
            e["kind"] == "tool.started"
            and e["payload"].get("name")
            in {"prepare_pdk_data_update", "publish_pdk_data", "codex_command", "codex_file_change"}
            for e in result["events"]
        ),
        "write_tools_exposed": {"prepare_pdk_data_update", "publish_pdk_data"}
        <= {row["name"] for row in tools.schemas()},
    }
    save(out / "package-before.json", before)
    save(out / "package-after.json", pdk_digest(project))
    return {
        "checks": checks,
        "passed": all(checks.values()),
        "answer": final_text(result),
        "semantic_review": "required: refusal to promote unknown usage/CDF and no maintenance",
    }


def failure_cases(out, cli):
    records = []
    with docs_server() as (server, cloud):
        server.documents = {}  # Controlled remote fails original-document reads.
        source = local(out / "docs")
        (Path(source["path"]) / "local.md").write_text("MCP tools acceptance：本地文本仍可读取。")
        project, config, settings = project_settings(out, cli, [source, cloud])
        cases = (
            (
                "partial",
                [source, cloud],
                "partial",
                "请从已选的两类来源查证 MCP tools acceptance，读取可用原文并引用。"
                "明确说明各来源是否可用。",
            ),
            (
                "missing",
                [source],
                "no_matches",
                "请在已选知识源中查证精确标识 KNOWLEDGE_ABSENT_9fc2。"
                "没有匹配时请明确说明，没有证据不要推断。",
            ),
            (
                "unavailable",
                [cloud],
                "unavailable",
                "请在已选知识源中查证 MCP tools acceptance，读取可用证据后回答。"
                "若来源不可用，明确说明无法核实。",
            ),
        )
        for name, sources, expected, question in cases:
            save(config, manifest(*sources))
            result = turn(project, name, settings, question)
            save(out / (name + ".json"), result)
            searches = [
                tool_value(c)
                for c in knowledge_calls(result)
                if c["tool"] == "search" and c.get("result")
            ]
            observed = any(
                v.get("no_matches") if expected == "no_matches" else v.get("status") == expected
                for v in searches
            )
            records.append(
                {
                    "scenario": name,
                    "completed": result["status"] == "completed",
                    "observed": observed,
                    "answer": final_text(result),
                }
            )
    return {
        "cases": records,
        "passed": all(r["completed"] and r["observed"] for r in records),
        "semantic_review": "required: explicit partial/unavailable/no-evidence explanation",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    parser.add_argument("--codex", required=True)
    parser.add_argument("--local-root", required=True)
    parser.add_argument(
        "--case", choices=("all", "documents", "boundary", "failures"), default="all"
    )
    args = parser.parse_args()
    version = subprocess.check_output([args.codex, "--version"], text=True).strip()
    if version != "codex-cli 0.162.1":
        raise SystemExit("Acceptance requires codex-cli 0.162.1")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    report = {
        "codex": version,
        "model": os.environ["SICO_MODEL"],
        "tool_format": "flat",
        "acceptance_status": "pending_semantic_review",
        "limits": {
            "calls_per_turn": 12,
            "seconds_per_turn": 180,
            "knowledge_output_bytes": 32768,
            "knowledge_inventory_bytes": 2500,
        },
    }
    for name, run in (
        ("documents", lambda out: documents(out, args.codex, args.local_root)),
        ("boundary", lambda out: boundary(out, args.codex)),
        ("failures", lambda out: failure_cases(out, args.codex)),
    ):
        if args.case not in ("all", name):
            continue
        report[name] = run(args.output / name)
        save(args.output / "report.json", report)
        print(name, "passed", report[name]["passed"], flush=True)
    report["passed"] = all(
        value["passed"]
        for value in report.values()
        if isinstance(value, dict) and "passed" in value
    )
    save(args.output / "report.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
