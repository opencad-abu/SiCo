"""One collection publisher for live and retained-capture completion."""

import datetime
import fcntl
import json

from ..template_build import make_template, publish_bytes
from ..template_capture import read_bytes
from ..template_capture_diagnostics import geometry_issues
from ..template_extract_reuse import extract_reuse
from ..template_schema import TemplateError, canonical
from .codec import VERSION, encode, logical, relative, sha, sync_directory
from .content import compile_documents
from .identity import reference
from .versions import CURRENT, LEGACY, collector


def private_bytes(root, folder, suffix, raw):
    digest = sha(raw)[7:]
    path = relative(root, folder + "/" + digest + suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise TemplateError("immutable private evidence conflict")
    else:
        try:
            publish_bytes(path, raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise TemplateError("immutable private evidence conflict")
    sync_directory(path.parent)
    return digest


def recipe(root, capture, options, category, provenance):
    scope = dict(
        collector=CURRENT,
        capture_sha256=capture["sha256"],
        options=options,
        category=category,
        provenance=provenance or {},
    )
    raw = canonical(scope).encode()
    key = sha(raw)[7:]
    path = relative(root, "evidence/" + key + ".request.json")
    with (root / ".request.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            value = dict(scope=scope, at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            path.parent.mkdir(parents=True, exist_ok=True)
            publish_bytes(path, canonical(value).encode())
            sync_directory(path.parent)
        value = json.loads(read_bytes(path))
    if value["scope"] != scope:
        raise TemplateError("immutable collection request conflict")
    return value, key


def compile_package(capture, options, request, request_key):
    scope, at = request["scope"], request["at"]
    version = collector(request)
    record = make_template(
        capture,
        category=scope["category"],
        provenance=scope["provenance"],
        classifications=options.get("classifications"),
    )
    issues, reuse = geometry_issues(capture), None
    if "reuse" in options:
        record, reuse = extract_reuse(record, options["reuse"], issues)
    capture_digest = "sha256:" + capture["sha256"]
    dependencies = dict(
        capture=dict(
            kind="file",
            target=dict(file_ref="capture"),
            fingerprint=capture_digest,
            method="sha256-bytes-v1",
        )
    )
    docs = compile_documents(record, capture, options, dependencies, current=version != LEGACY)
    env = dict(source="capture", depends_on=["capture"])
    sources = dict(
        capture=dict(
            kind="capture",
            at=at,
            summary="Saved OA capture facts",
            collector=version,
            scope_digest=capture_digest,
            evidence_ref="capture:" + capture["sha256"],
        ),
        intent=dict(
            kind="user",
            at=at,
            summary="Explicit collection request and supplied classification/reuse scope",
            confirmed_by="requesting-user",
            scope_digest=sha(canonical(scope).encode()),
            evidence_ref="collection-request:" + request_key,
        ),
    )
    if "relation" in docs:
        sources["relations"] = dict(
            kind="derived",
            at=at,
            summary="Observed placement preferences",
            inputs=["capture"],
            method="sico-relations-flat-v1",
        )
    if docs.get("symbol", {}).get("source") == "symbol_interface":
        from .symbol_checks import interface_evidence

        sources["symbol_interface"] = dict(
            kind="derived",
            at=at,
            inputs=["capture"],
            summary="Independent saved symbol interface and geometry",
            method="saved-symbol-interface-v1",
            evidence_ref=interface_evidence(docs["symbol"], capture_digest),
        )
    category = scope["category"]
    authorized = bool(options.get("library") and options.get("cell"))
    # An authored pattern is a human-written reference, not a captured OA circuit:
    # its source is a document, and its declared license is the only permission.
    authored = options.get("authored")
    permitted = authorized or bool(authored)
    resources = dict(
        capture=dict(
            kind="private_evidence",
            root="private",
            path="captures/" + capture["sha256"] + ".jsonl",
            purpose="Retained source capture",
            dependency="capture",
        )
    )
    if authored:
        dependencies["capture"]["target"] = dict(file_ref="authored")
        sources["capture"] = dict(
            kind="document",
            at=at,
            summary="Authored passive reference pattern; not an OA capture",
            file_ref="authored",
            locator=authored["locator"],
            evidence_ref="collection-request:" + request_key,
        )
        sources["license"] = dict(
            kind="document",
            at=at,
            summary=authored["license_summary"],
            file_ref="license",
            locator=authored["license_locator"],
        )
        resources = dict(
            authored=dict(
                kind="document",
                root="builder",
                path=authored["source_path"],
                purpose="Authored pattern generation script",
                dependency="capture",
            ),
            license=dict(
                kind="document",
                root="builder",
                path=authored["license_path"],
                purpose="Declared license for the authored pattern",
            ),
        )
    entry = dict(
        name=record["source"]["cell"],
        description="Direct source circuit reference",
        categories=[category],
        granularity="unknown",
        detail_level="raw",
        dir="template",
        use=dict(
            reference="allow" if permitted else "unknown",
            reuse="allow" if (authorized and "reuse" in options) or authored else "unknown",
        ),
    )
    if options.get("legacy_ref"):
        entry["derived_from"] = [options["legacy_ref"]]
        docs["collection"]["legacy_refs"] = [options["legacy_ref"]]
    if authored:
        entry["reason"] = dict(reuse="Authored pattern declares no validated reuse contract")
    elif "reuse" not in options or not authorized:
        entry["reason"] = dict(reuse="No explicit authorized reuse intent was supplied")
    evidence_pointer = {"/items/main/use": "intent"}
    if authored:
        sources["intent"] = dict(
            kind="derived",
            at=at,
            summary="Authored reference pattern scope",
            inputs=["capture"],
            method="authored-builder-v1",
        )
        evidence_pointer = {"/items/main/use": "license"}
    elif not authorized:
        entry.setdefault("reason", {})["reference"] = "Legacy input usage permission is unknown"
        sources["intent"] = dict(
            kind="derived",
            at=at,
            summary="Unconfirmed imported source scope",
            inputs=["capture"],
            method="legacy-import-v1",
            evidence_ref="collection-request:" + request_key,
        )
    tables = dict(
        category=dict(**env, items={category: dict(name=category)}),
        template=dict(
            source="capture",
            depends_on=["capture"],
            evidence=evidence_pointer,
            items=dict(main=entry),
        ),
        file=dict(**env, items=resources),
        sources=dict(items=sources),
    )
    if "layout" in record["assets"]:
        from .assets import native_layout

        docs["layout"] = native_layout(record, dependencies, tables["file"]["items"])
    if version == CURRENT:
        from .layout import project as project_layout
        from .native import apply

        if "layout" in docs:
            project_layout(record, docs)
        apply(capture, docs, dependencies, tables["file"]["items"])
    rules = {
        r["id"]: dict(
            name=r["id"],
            version=r["version"],
            implementation_digest="sha256:" + r["implementation_digest"],
        )
        for r in record.get("reuse_contract", {}).get("allowed_rule_refs", [])
    }
    package = dict(
        format="sico.circuit-template.package",
        schema_version=VERSION,
        package_id="source-" + sha(encode(docs["collection"]["origin"]))[7:31],
        revision="pending",
        created_at=at,
        dependencies=dependencies,
        pdks={},
        rules=rules,
        files={},
        required_features=[],
    )
    entry["template_ref"] = reference(package, tables, docs, "main")
    from .qualification import receipt

    report_raw = receipt(
        record,
        docs,
        dependencies,
        sources,
        version,
        permitted,
        entry["template_ref"],
        at,
        request_key,
        capture["sha256"],
    )
    return package, tables, docs, record, reuse, report_raw


def collect(catalog, capture, raw, options, *, category="reference", provenance=None):
    from .package import publish

    root = catalog.ensure_private_destination()
    private_bytes(root, "captures", ".jsonl", raw)
    request, key = recipe(root, capture, options, category, provenance)
    package, tables, docs, record, reuse, report = compile_package(capture, options, request, key)
    private_bytes(root, "evidence", ".validation.json", report)
    roots = dict(private=str(root.resolve()))
    if "layout" in record["assets"]:
        roots["source_library"] = record["assets"]["layout"]["source"]["library_path"]
    path = publish(root, package, tables, {"main": docs}, roots=roots)
    ref = tables["template"]["items"]["main"]["template_ref"]
    result = dict(
        ok=True,
        stage="stored_private",
        schema_version=VERSION,
        template_ref=ref,
        package=str(path),
        revision=logical(dict(base=sha((path / "package.json").read_bytes()))),
        capture=str(root / "captures" / (capture["sha256"] + ".jsonl")),
        capture_sha256=capture["sha256"],
        catalog_origin=dict(tier="private", root=str(root), catalog=str(path / "package.json")),
        capabilities={k: v["result"] for k, v in docs["qualification"]["checks"].items()},
    )
    issues = geometry_issues(capture)
    if issues:
        result["diagnostics"] = dict(issues=issues[:8], issue_count=len(issues))
    if reuse is not None:
        capability = docs["qualification"]["checks"][options["reuse"]["mode"] + "-reuse-v1"]
        success = capability["result"] == "passed"
        result["reuse"] = reuse | dict(template_ref=ref if success else None)
        if not success:
            result.update(ok=False, stage="stored_reference", code="template_reuse_unavailable")
            if reuse["status"] in {"needs_classification", "needs_contract"}:
                result["continuation"] = dict(
                    tool="extract_circuit_templates",
                    arguments=dict(template_ref=ref, reuse=options["reuse"]),
                )
    for section in ("reuse", "diagnostics"):
        for row in result.get(section, {}).get("issues", []):
            if "message" in row:
                row["message"] = row["message"][:256]
    if len(canonical(result).encode()) > 16384:
        result["details_ref"] = "validation:" + sha(report)[7:]
        for section in ("reuse", "diagnostics"):
            rows = result.get(section, {}).get("issues", [])
            while rows and len(canonical(result).encode()) > 16384:
                rows.pop()
    return result


def retained(catalog, args):
    from .reader import locate

    item, _ = locate(catalog, args["template_ref"])
    from .replay import replay

    _, capture, raw, scope = replay(item)
    previous = scope["options"].get("classifications", {})
    supplied = args.get("classifications", {})
    if any(name in previous and previous[name] != row for name, row in supplied.items()):
        raise TemplateError("completion cannot replace pinned classification evidence")
    options = scope["options"] | dict(reuse=args["reuse"], classifications=previous | supplied)
    result = collect(
        catalog, capture, raw, options, category=scope["category"], provenance=scope["provenance"]
    )
    return result | dict(completed_from=args["template_ref"], live=False)
