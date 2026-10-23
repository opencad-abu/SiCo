"""Bind guarded native content observations to the flat dependency authority."""

from ..template_native_content import METHOD, fingerprint
from .codec import encode, fail, sha


def apply(capture, docs, dependencies, resources):
    views = {}
    for asset, raw in capture["assets"].items():
        for row in raw["header"].get("native_content", {}).get("views", []):
            key = tuple(row["target"])
            value = fingerprint(row)
            # Two views of the same target must not disagree within one bundle.
            observed = {k: v for k, v in row.items() if k not in {"role", "library_path"}}
            if key in views and views[key][1] != observed:
                fail("native dependency differs between captured assets")
            views[key] = (value, observed)
    for dep in dependencies.values():
        if dep["kind"] == "master":
            key = tuple(dep["target"][k] for k in ("library", "cell", "view"))
            observed = views.get(key, (None, None))[1]
            value = fingerprint(observed | dict(role="master")) if observed else None
            if value:
                dep.update(fingerprint=value, method=METHOD)
    for name, raw in capture["assets"].items():
        header = raw["header"]
        target = dict(
            zip(("library", "cell", "view"), (header[k] for k in ("lib", "cell", "view")))
        )
        own = views.get(tuple(target.values()), (None,))[0]
        key = "layout" if name == "layout" else "source_" + name
        dependencies[key] = dict(
            kind=name,
            target=target,
            method=METHOD,
            fingerprint=own
            or dict(
                state="unknown", reason="Native bytes or static dependency coverage unavailable"
            ),
        )
        scope = docs["collection"]["assets"][name]["dependencies"]
        scope.append(key)
        if name == "layout":
            layout = docs["layout"]
            resources["layout"]["dependency"] = key
            dependencies[key]["target"] = dict(file_ref="layout")
            for row in header.get("native_content", {}).get("views", []):
                if row["role"] != "master":
                    continue
                target = dict(zip(("library", "cell", "view"), row["target"]))
                master = "m" + sha(encode(target))[7:23]
                dependencies.setdefault(
                    master,
                    dict(
                        kind="master",
                        target=target,
                        method=METHOD,
                        fingerprint=fingerprint(row)
                        or dict(
                            state="unknown",
                            reason="Parameterized/hierarchical native master is unresolved",
                        ),
                    ),
                )
                layout["depends_on"].append(master)
                scope.append(master)
            # Every captured instance must have its own observed master identity.
            declared = {
                tuple(r["target"])
                for r in header.get("native_content", {}).get("views", [])
                if r["role"] == "master"
            }
            actual = {
                tuple(r[k] for k in ("libName", "cellName", "viewName"))
                for r in raw["rows"]
                if r["kind"] == "instance"
            }
            if (
                not own
                or actual != declared
                or any(
                    not isinstance(dependencies[k]["fingerprint"], str)
                    for k in layout["depends_on"]
                )
            ):
                gap(docs, "Native layout bytes or direct dependency identities are incomplete")
            else:
                # The v1/v2 placeholder is replaced only after the native validator ran.
                docs["collection"]["gaps"] = {
                    k: v
                    for k, v in docs["collection"]["gaps"].items()
                    if v["reason"]
                    != "Native layout content identity and XL bindings are unverified"
                }
            layout["depends_on"] = sorted(set(layout["depends_on"]))
        scope[:] = sorted(set(scope))


def gap(docs, reason):
    rows = docs["collection"]["gaps"]
    key = "native_layout_" + str(len(rows))
    rows[key] = dict(
        part="layout.json",
        object="asset",
        pointer="",
        code="missing_required",
        operations=["layout-reference-v1"],
        reason=reason,
        action="collect_source",
    )


def layout_issues(docs, dependencies):
    layout = docs.get("layout")
    if not layout:
        return ["layout_asset_missing"]
    if layout["dependency"] not in layout["depends_on"]:
        return ["layout_dependency_not_bound"]
    if any(not isinstance(dependencies[k]["fingerprint"], str) for k in layout["depends_on"]):
        return ["layout_identity_incomplete"]
    if any("layout-reference-v1" in r["operations"] for r in docs["collection"]["gaps"].values()):
        return ["layout_capture_has_gaps"]
    return []
