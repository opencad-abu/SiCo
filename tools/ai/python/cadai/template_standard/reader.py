"""Fresh verified standard snapshots and revision-bound bounded semantic pages."""

import base64
import json
from pathlib import Path

from ..template_schema import TemplateUnavailable
from .codec import LIMIT, VERSION, encode, fail, logical, read, relative, sha
from .overlay import FORMAT as OVERLAY_FORMAT
from .overlay import merge as overlay_merge
from .overlay import read_overlay
from .overlay import resolve as overlay_resolve
from .package import index, load, verify
from .qualification import PROFILES


def available(catalog):
    return any((loc.path / "index.json").exists() for loc in catalog.locations)


def installation_roots_for(directory):
    """The installation roots of one package directory recorded by its own root."""
    directory = Path(directory)
    root = directory.parent
    if not (root / "index.json").exists():
        raise TemplateUnavailable("standard root is no longer installed: " + str(root))
    return installation_roots(root, directory)


def base_resolver(catalog):
    """Resolve an overlay's fixed base triple from the configured indexes only."""
    state = {}

    def triples():
        if "value" not in state:
            found = {}
            for location in catalog.locations:
                if not (location.path / "index.json").exists():
                    continue
                installed, _observed = index(location.path)
                for installation in installed["packages"].values():
                    directory = relative(location.path, installation["path"])
                    value, digest = read(directory / "package.json")
                    if value.get("format") == OVERLAY_FORMAT:
                        continue
                    key = (value.get("package_id"), value.get("revision"), digest)
                    if key in found and found[key] != directory:
                        fail("overlay base is ambiguous: " + str(value.get("package_id")))
                    found[key] = directory
            state["value"] = found
        return state["value"]

    def resolve(base):
        key = (base["package_id"], base["revision"], base["manifest_digest"])
        directory = triples().get(key)
        if directory is None:
            fail("overlay base is not installed: " + str(base["package_id"]))
        return directory, installation_roots_for(directory)

    return resolve


def snapshot(catalog):
    items, revision, _, _ = verified(catalog)
    return items, revision


def verified(catalog):
    """Fresh verified snapshot plus the observed digest sets it was built from."""
    resolve = base_resolver(catalog)
    items, roots, members = {}, [], []
    for location in catalog.locations:
        if not (location.path / "index.json").exists():
            continue
        located, observed, located_members = read_location(location, resolve)
        roots.append((location.path, observed))
        members.extend(located_members)
        for ref, item in located.items():
            previous = items.get(ref)
            if previous is None:
                items[ref] = item
            else:
                if (
                    previous["documents"]["qualification"]
                    != item["documents"]["qualification"]
                ):
                    fail("conflicting qualification receipts for fixed reference")
                if item["origin"] not in previous["origins"]:
                    previous["origins"].append(item["origin"])
            if len(items) > 10000:
                fail("snapshot exceeds 10000 templates")
    return items, revision_of(roots, members), roots, members


def read_location(location, resolve_base=None):
    """Load and verify exactly one installed root; overlays merge over their base."""
    installed, observed = index(location.path)
    items, members = {}, []
    classified = []
    for entry in installed["packages"].values():
        if set(entry) != {"path", "roots"}:
            fail("invalid installation")
        directory = relative(location.path, entry["path"])
        head, _ = read(directory / "package.json")
        classified.append((directory, head, entry))
    full = [row for row in classified if row[1].get("format") != OVERLAY_FORMAT]
    overlays = sorted(
        (row for row in classified if row[1].get("format") == OVERLAY_FORMAT),
        key=lambda row: row[0].name,
    )

    def resolve(base, directory):
        if resolve_base is not None:
            return resolve_base(base)
        found = overlay_resolve(base, [directory] + [row[0] for row in classified])
        return found, installation_roots_for(found)

    for directory, head, entry in [*full, *overlays]:
        if head.get("format") == OVERLAY_FORMAT:
            base_dir, base_roots = resolve(head["base"], directory)
            base_package = load(base_dir)
            merged = overlay_merge(base_dir, directory)
            overlay_observed = read_overlay(directory)["observed"]
            roots = dict(base_roots)
            roots.update(entry["roots"])
            members.append((directory, overlay_observed))
            members.append((base_package["path"], base_package["observed"]))
            located = dict(
                **merged,
                observed=overlay_observed,
                roots=roots,
                origin=location.describe(),
                origins=[location.describe()],
                path=str(directory),
                overlay_base=str(base_dir),
            )
            for ident, docs in merged["templates"].items():
                template = merged["tables"]["template"]["items"][ident]
                if template["detail_level"] != "raw" and roots:
                    fail("public projection installation cannot expose private resource roots")
                items[template["template_ref"]] = dict(
                    **located, template_id=ident, entry=template, documents=docs
                )
            continue
        package = load(directory)
        members.append((package["path"], package["observed"]))
        for ident, docs in package["templates"].items():
            template = package["tables"]["template"]["items"][ident]
            if template["detail_level"] != "raw" and entry["roots"]:
                fail("public projection installation cannot expose private resource roots")
            items[template["template_ref"]] = dict(
                **package,
                template_id=ident,
                entry=template,
                documents=docs,
                roots=entry["roots"],
                origin=location.describe(),
                origins=[location.describe()],
            )
    verify(location.path, observed)
    for root, files in members:
        verify(root, files)
    return items, observed, members


def revision_of(roots, members):
    return logical(
        dict(
            indexes=[{"root": str(root), "files": observed} for root, observed in roots],
            packages=[observed["package.json"] for _, observed in members],
        )
    )


def catalog_revision(catalog):
    """The fixed catalog revision from manifests only, without reading documents."""
    roots, members, triples, overlays = [], [], set(), []
    for location in catalog.locations:
        if not (location.path / "index.json").exists():
            continue
        installed, observed = index(location.path)
        roots.append((location.path, observed))
        for installation in installed["packages"].values():
            directory = relative(location.path, installation["path"])
            value, digest = read(directory / "package.json")
            members.append((directory, {"package.json": digest}))
            if value.get("format") == OVERLAY_FORMAT:
                overlays.append(value["base"])
            else:
                triples.add((value.get("package_id"), value.get("revision"), digest))
    for base in overlays:
        if (base["package_id"], base["revision"], base["manifest_digest"]) not in triples:
            fail("overlay base is not installed: " + str(base["package_id"]))
    return revision_of(roots, members)


def locate(catalog, ref):
    """Resolve one reference without verifying unrelated roots or packages."""
    from . import derived

    rows, revision = derived.rows(catalog)
    if rows is None:
        items, revision = snapshot(catalog)
        if ref not in items:
            raise TemplateUnavailable("standard template reference unavailable: " + ref)
        return items[ref], revision
    row = rows.get(ref)
    if row is None:
        raise TemplateUnavailable("standard template reference unavailable: " + ref)
    return item_at(row), revision


def item_at(row):
    """Load and verify the single package that holds one derived row."""
    path = Path(row["package"])
    head, _ = read(path / "package.json")
    if head.get("format") == OVERLAY_FORMAT:
        base = Path(row["overlay_base"]) if row.get("overlay_base") else None
        if base is None:
            candidates = []
            for loc in row.get("origins", []):
                root = Path(loc["root"])
                if not (root / "index.json").exists():
                    continue
                installed, _observed = index(root)
                candidates.extend(
                    relative(root, entry["path"]) for entry in installed["packages"].values()
                )
            base = overlay_resolve(head["base"], candidates)
        base_package = load(base)
        verify(base, base_package["observed"])
        overlay_observed = read_overlay(path)["observed"]
        verify(path, overlay_observed)
        merged = overlay_merge(base, path)
        location = next(
            (loc for loc in row["origins"] if Path(loc["root"]) in path.parents),
            row["origin"],
        )
        if not (Path(location["root"]) / "index.json").exists():
            raise TemplateUnavailable("standard root is no longer installed: " + location["root"])
        roots = dict(installation_roots_for(base))
        roots.update(installation_roots(Path(location["root"]), path))
        return dict(
            **merged,
            observed=overlay_observed,
            template_id=row["template_id"],
            entry=merged["tables"]["template"]["items"][row["template_id"]],
            documents=merged["templates"][row["template_id"]],
            roots=roots,
            origin=row["origin"],
            origins=row["origins"],
        )
    package = load(path)
    verify(path, package["observed"])
    location = next(
        (loc for loc in row["origins"] if Path(loc["root"]) in Path(row["package"]).parents),
        row["origin"],
    )
    if not (Path(location["root"]) / "index.json").exists():
        raise TemplateUnavailable("standard root is no longer installed: " + location["root"])
    return dict(
        **package,
        template_id=row["template_id"],
        entry=package["tables"]["template"]["items"][row["template_id"]],
        documents=package["templates"][row["template_id"]],
        roots=installation_roots(Path(location["root"]), Path(row["package"])),
        origin=row["origin"],
        origins=row["origins"],
    )


def installation_roots(root, package):
    installed, observed = index(root)
    names = {name: digest for name, digest in observed.items()}
    verify(root, names)
    for row in installed["packages"].values():
        if relative(root, row["path"]) == package:
            return row["roots"]
    fail("installation does not list the requested package")


def summary(item):
    entry, docs = item["entry"], item["documents"]
    origin = docs["collection"].get("origin", {})
    top = docs.get("topology", {})
    return dict(
        template_ref=entry["template_ref"],
        name=entry["name"],
        description=entry["description"],
        categories=entry["categories"],
        detail_level=entry["detail_level"],
        use=entry["use"],
        library=origin.get("library", "unknown"),
        cell=origin.get("cell", entry["name"]),
        counts={k: len(top.get(k, {})) for k in ("devices", "nets", "ports")},
        capabilities={k: r["result"] for k, r in docs["qualification"]["checks"].items()},
        origin=item["origin"],
        available_from=item["origins"],
    )


def page(rows, args, revision):
    query = {k: v for k, v in args.items() if k not in {"cursor", "offset"}}
    start = args.get("offset", 0)
    if args.get("cursor"):
        try:
            cursor = json.loads(base64.urlsafe_b64decode(args["cursor"]))
        except (ValueError, UnicodeError):
            fail("invalid cursor")
        if cursor.get("revision") != revision or cursor.get("query") != logical(query):
            fail("stale cursor: query or revision changed")
        start = cursor["offset"]
    elif start:
        fail("standard continuation requires next_cursor, not an unpinned offset")
    limit = min(args.get("limit", 20), 100)
    result = dict(
        ok=True,
        schema_version=VERSION,
        revision=revision,
        status="ok",
        items=[],
        returned=0,
        complete=True,
        total=len(rows),
    )
    for row in rows[start : start + limit]:
        candidate = result | dict(items=[*result["items"], row])
        if len(encode(candidate)) > LIMIT - 2500:
            if not result["items"]:
                fail("one response row exceeds budget; select a named entity")
            break
        result["items"].append(row)
    result["returned"] = len(result["items"])
    end = start + result["returned"]
    result["complete"] = end >= len(rows)
    if not result["complete"]:
        result["next_cursor"] = base64.urlsafe_b64encode(
            json.dumps(dict(revision=revision, query=logical(query), offset=end)).encode()
        ).decode()
    if len(encode(result)) > LIMIT:
        fail("response exceeds 16 KiB")
    return result


def query(catalog, args):
    from . import derived

    entries, revision = derived.rows(catalog)
    if entries is None:
        items, revision = snapshot(catalog)
        entries = {ref: derived.row(item) for ref, item in items.items()}
    rows = []
    for entry in entries.values():
        row = derived.response(entry)
        if any(
            args.get(k) and args[k] != entry[k]
            for k in ("library", "cell", "detail_level")
        ):
            continue
        if args.get("tier"):
            selected = [o for o in entry["origins"] if o["tier"] == args["tier"]]
            if not selected:
                continue
            row["origin"] = selected[0]
        if args.get("category") and args["category"] not in entry["categories"]:
            continue
        if args.get("device_kind") and args["device_kind"] not in entry["device_kinds"]:
            continue
        count = entry["counts"]["devices"]
        if count < args.get("min_devices", 0) or count > args.get("max_devices", 100000):
            continue
        if args.get("query") and any(
            word not in (row["library"] + " " + row["cell"] + " " + row["description"]).lower()
            for word in args["query"].lower().split()
        ):
            continue
        if args.get("required_asset") and args["required_asset"] not in entry["assets"]:
            continue
        if args.get("topology_fingerprint") and (
            not entry["topology_fingerprint"]
            or entry["topology_fingerprint"] != args["topology_fingerprint"]
        ):
            continue
        if args.get("rule_version"):
            continue  # Legacy normalizer versions do not label standard packages.
        if args.get("purpose", "reference") != "investigate":
            purpose = args.get("purpose", "reference")
            profile = args.get(
                "capability", "topology-v1" if purpose == "reference" else "whole-reuse-v1"
            )
            if (
                entry["use"][purpose] != "allow"
                or entry["capabilities"][profile] != "passed"
            ):
                continue
        rows.append(row)
    rows.extend(legacy_tiers(catalog, args))
    return page(sorted(rows, key=lambda r: (r["name"], r["template_ref"])), args, revision)


def legacy_tiers(catalog, args):
    """Un-migrated tiers keep answering default queries until they are migrated.

    A location that has a standard index is authoritative for itself; the legacy
    catalogs next to an installed root are superseded by it.  Locations without a
    standard index keep their pre-migration rows, marked legacy and unqualified so
    callers never mistake them for standard, capability-checked templates.
    """
    if args.get("capability") or args.get("purpose") == "reuse":
        return []
    entries = [
        (path, locations)
        for path, locations in catalog._entries()
        if all(not (location.path / "index.json").exists() for location in locations)
    ]
    if not entries:
        return []
    from ..template_query import query_rows

    rows = []
    for summary in query_rows(catalog, args, entries):
        origin = summary.get("catalog_origin", {})
        counts = summary.get("counts", {})
        rows.append(
            dict(
                template_ref=summary["template_ref"],
                name=summary["cell"],
                description="Legacy catalog reference; not migrated to the standard format",
                categories=[summary["category"]],
                detail_level=summary.get("detail_level", "raw"),
                use={"reference": "unknown", "reuse": "unknown"},
                library=summary["library"],
                cell=summary["cell"],
                counts={k: counts.get(k, 0) for k in ("devices", "nets", "ports")},
                capabilities={profile: "unknown" for profile in PROFILES},
                origin=origin,
                available_from=summary.get("available_from", [origin]),
                legacy=True,
            )
        )
    return rows


def detail(catalog, args):
    item, revision = locate(catalog, args["template_ref"])
    section = {
        "schematic": "placement",
        "relations": "relation",
        "routing_style": "routing",
        "parameters": "parameter",
        "reuse_contract": "reuse",
    }.get(args["section"], args["section"])
    if section == "summary":
        return page([summary(item)], args, revision)
    doc = item["documents"].get(section, item["tables"].get(section))
    if doc is None:
        return page([], args, revision) | dict(status="not_available")
    rows = []
    for key, value in doc.items():
        if args["entity"] not in {"all", key}:
            continue
        if isinstance(value, dict):
            rows.extend(
                dict(part=section, entity=key, id=ident, data=row)
                for ident, row in sorted(value.items())
            )
        else:
            rows.append(dict(part=section, entity=key, data=value))
    return page(rows, args, revision)
