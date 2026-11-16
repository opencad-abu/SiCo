"""Opt-in AS6 JedAI acceptance; never collected by pytest and no site data baked in.

Everything comes from the user/administrator's private inputs:
JEDAI_MCP_URL (the AAI MCP endpoint they choose), JEDAI_ACCESS_TOKEN (dedicated
named-key token that must match the production manifest credential_ref),
--collections and --queries (or JEDAI_COLLECTIONS / JEDAI_QUERIES). Embedding is
configured on the JedAI side; this runner never stores an address, collection,
query or credential in the repository. Output belongs in a new private directory
outside version control.
"""

import argparse
import json
import os
from pathlib import Path

from knowledge_fixtures import local, manifest
from sico.knowledge.engine import Knowledge

CREDENTIAL = "JEDAI_ACCESS_TOKEN"


def save(path, value, token):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if token and token in text:
        raise SystemExit("credential value would enter evidence; refusing to write")
    path.write_text(text, encoding="utf-8")


def remote_source(collection, url, credential=CREDENTIAL):
    return {"id": "jedai-" + collection, "name": "JedAI " + collection, "type": "remote",
            "adapter": "jedai_aai_mcp", "url": url, "collection": collection,
            "source": "project", "enabled": True, "timeout_sec": 30,
            "credential_ref": credential}


def summarize(hit):
    return {"source_id": hit["source_id"], "uri": hit["uri"], "title": hit["title"],
            "version": hit["version"], "excerpt": hit["excerpt"][:160]}


def check_collection(project, url, token, collection, queries):
    source = remote_source(collection, url)
    engine = Knowledge(project, manifest(source), {CREDENTIAL: token})
    status = engine.status()["sources"][0]
    record = {"collection": collection, "status": status["status"],
              "message": status.get("message", ""), "queries": []}
    for query in queries:
        result = engine.search(query, limit=4)
        entry = {"query": query, "status": result["status"], "no_matches": result["no_matches"],
                 "hits": [summarize(hit) for hit in result["hits"][:4]]}
        if result["hits"]:
            value = engine.read(source["id"], result["hits"][0]["evidence_id"])
            entry["read"] = {"verified": bool(value.get("verified")),
                             "status": value.get("status", "verified"),
                             "uri": value.get("uri"),
                             "text_bytes": len(value.get("text", "").encode())}
        record["queries"].append(entry)
    record["passed"] = bool(record["queries"]) and all(
        row["hits"] and row["status"] in {"ready", "partial"} and
        row.get("read", {}).get("verified") for row in record["queries"])
    return record


def check_mixed(project, url, token, query, remote_collection):
    source = local(project / "local-docs")
    (Path(source["path"]) / "mixed.md").write_text(query + "：本地混检文本。")
    remote = remote_source(remote_collection, url)
    engine = Knowledge(project, manifest(source, remote), {CREDENTIAL: token})
    assert engine.build("local")["status"] == "ready"
    result = engine.search(query)
    ready = {"status": result["status"],
             "sources": sorted(hit["source_id"] for hit in result["hits"])}
    missing = remote_source(remote_collection, url, "JEDAI_MISSING_TOKEN")
    partial = Knowledge(project, manifest(source, missing), {}).search(query)
    record = {
        "ready": ready,
        "partial": {"status": partial["status"],
                    "hits": sorted({hit["source_id"] for hit in partial["hits"]}),
                    "jedai": next(row["status"] for row in partial["sources"]
                                  if row["source_id"] == remote["id"])},
    }
    record["passed"] = (
        ready["status"] == "ready" and set(ready["sources"]) == {"local", remote["id"]}
        and partial["status"] == "partial" and set(record["partial"]["hits"]) == {"local"}
        and record["partial"]["jedai"] == "auth_required"
    )
    return record


def queries_input(args):
    if args.queries:
        return json.loads(args.queries.read_text(encoding="utf-8"))
    return json.loads(Path(os.environ["JEDAI_QUERIES"]).read_text()) if os.environ.get(
        "JEDAI_QUERIES") else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    parser.add_argument("--case", choices=("all", "status", "search", "mixed"), default="all")
    parser.add_argument("--collections", default=os.environ.get("JEDAI_COLLECTIONS", ""))
    parser.add_argument("--queries", type=Path, help="Private JSON map collection -> [query]")
    parser.add_argument("--mixed-query", default="", help="Query mixing a local note with JedAI")
    args = parser.parse_args()
    url, token = os.environ.get("JEDAI_MCP_URL", ""), os.environ.get(CREDENTIAL, "")
    if not url or not token:
        raise SystemExit("set JEDAI_MCP_URL and JEDAI_ACCESS_TOKEN before acceptance")
    collections = [value.strip() for value in args.collections.split(",") if value.strip()]
    if not collections:
        raise SystemExit("provide --collections or JEDAI_COLLECTIONS")
    queries = queries_input(args)
    if args.case in ("all", "search"):
        absent = [name for name in collections if not queries.get(name)]
        if absent:
            raise SystemExit("provide queries (--queries) for: " + ", ".join(absent))
    mixed_query = args.mixed_query or (queries.get(collections[0]) or [""])[0]
    if args.case in ("all", "mixed") and not mixed_query:
        raise SystemExit("provide --mixed-query or queries for the first collection")
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    project = args.output / "project"
    project.mkdir(mode=0o700)
    report = {"endpoint": url, "collections": collections, "case": args.case,
              "acceptance_status": "pending_semantic_review"}
    for collection in collections:
        source = remote_source(collection, url)
        if args.case in ("all", "status"):
            engine = Knowledge(project, manifest(source), {CREDENTIAL: token})
            report.setdefault("status", {})[collection] = engine.status()["sources"][0]
        if args.case in ("all", "search"):
            report.setdefault("search", {})[collection] = check_collection(
                project, url, token, collection, queries[collection])
        save(args.output / "report.json", report, token)
    if args.case in ("all", "mixed"):
        report["mixed"] = check_mixed(project, url, token, mixed_query, collections[0])
        save(args.output / "report.json", report, token)
    outcomes = [row["passed"] for row in report.get("search", {}).values()]
    outcomes.extend(row["status"] == "ready" for row in report.get("status", {}).values())
    if "mixed" in report:
        outcomes.append(report["mixed"]["passed"])
    report["passed"] = bool(outcomes) and all(outcomes)
    save(args.output / "report.json", report, token)
    print("passed", report["passed"], flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
