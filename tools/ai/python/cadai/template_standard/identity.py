"""Appendix A.7 transitive identity projection; physical layout is excluded."""

import hashlib

from .codec import VERSION, fail, jcs

ROLES = (
    "collection",
    "topology",
    "placement",
    "routing",
    "relation",
    "parameter",
    "reuse",
    "symbol",
    "layout",
)


def escape(key):
    return key.replace("~", "~0").replace("/", "~1")


def pointer(document, path):
    if path == "":
        return document
    if not isinstance(path, str) or not path.startswith("/"):
        fail("invalid evidence pointer")
    node = document
    try:
        for token in path[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            node = node[int(token)] if isinstance(node, list) else node[token]
    except (KeyError, IndexError, ValueError, TypeError):
        fail("evidence pointer does not exist: " + path)
    return node


def envelope(document, prefix=""):
    source, longest, evidence = document["source"], -1, {}
    for path, value in document.get("evidence", {}).items():
        pointer(document, path)
        if path == prefix or prefix.startswith(path + "/") or path == "":
            if len(path) > longest:
                source, longest = value, len(path)
        elif path.startswith(prefix + "/"):
            evidence[path[len(prefix) :]] = value
    return dict(source=source, depends_on=document["depends_on"], evidence=evidence)


def projection(package, tables, documents, template_id):
    entry = tables["template"]["items"][template_id]
    docs = {key: value for key, value in documents.items() if key in ROLES}
    result = dict(
        format="sico.circuit-template.identity.v1",
        schema_version=VERSION,
        template_id=template_id,
        entry={k: v for k, v in entry.items() if k not in {"dir", "template_ref"}},
        entry_evidence=envelope(tables["template"], "/items/" + escape(template_id)),
        documents=docs,
        categories={},
        pdks={},
        rules={},
        dependencies={},
        resources={},
        sources={},
    )
    queue = []

    def add(kind, keys):
        queue.extend((kind, key) for key in keys)

    def evidence(env):
        add("sources", [env["source"], *env.get("evidence", {}).values()])
        add("dependencies", env["depends_on"])

    evidence(result["entry_evidence"])
    for doc in docs.values():
        evidence(doc)
    if isinstance(entry["categories"], list):
        add("categories", entry["categories"])
    for row in docs.get("topology", {}).get("masters", {}).values():
        add("dependencies", [row["dependency"]])
        if "pdk" in row:
            add("pdks", [row["pdk"]])
    for row in docs["collection"]["assets"].values():
        add("dependencies", row["dependencies"])
    add("rules", docs.get("reuse", {}).get("allowed_rules", []))
    if "layout" in docs:
        add("resources", [docs["layout"]["asset"]])
        add("dependencies", [docs["layout"]["dependency"]])
    while queue:
        kind, key = queue.pop()
        if key in result[kind]:
            continue
        table = {"sources": "sources", "resources": "file", "categories": "category"}.get(kind)
        available = tables[table]["items"] if table else package[kind]
        if key not in available:
            fail("dangling " + kind + " reference: " + str(key))
        row = available[key]
        if kind in {"categories", "resources"}:
            env = envelope(tables[table], "/items/" + escape(key))
            result[kind][key] = dict(data=row, evidence=env)
            evidence(env)
        else:
            result[kind][key] = row
        if kind == "categories" and "parent" in row:
            add("categories", [row["parent"]])
        elif kind == "resources" and "dependency" in row:
            add("dependencies", [row["dependency"]])
        elif kind == "dependencies" and "file_ref" in row["target"]:
            add("resources", [row["target"]["file_ref"]])
        elif kind == "sources":
            add("sources", row.get("inputs", []))
            if "file_ref" in row:
                add("resources", [row["file_ref"]])
    return result


def reference(package, tables, documents, template_id):
    return (
        "ctpl_"
        + hashlib.sha256(jcs(projection(package, tables, documents, template_id))).hexdigest()
    )
