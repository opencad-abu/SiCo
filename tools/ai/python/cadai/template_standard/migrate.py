"""One-way migration of retained legacy captures into immutable standard packages.

The legacy SQLite catalogs stay readable so fixed ``tpl_`` references never move.
This module only adds the standard form: each retained capture is compiled by the
same production compiler as a live collection, keeps its old reference through
``derived_from``/``legacy_refs``, and never invents a usage permission, an
electrical observation or a native content fingerprint.  Because no live
library/cell pair is supplied, the imported entry stays ``use=unknown``.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

from ..template_build import publish_bytes
from ..template_capture import parse_capture
from ..template_schema import canonical
from .codec import fail, relative, sha, sync_directory
from .collect import compile_package, private_bytes
from .package import publish
from .versions import CURRENT


def scope(legacy_ref, capture_sha256, category, provenance, authored=None):
    """Pinned offline import scope; the absence of library/cell is the permission gap."""
    options = dict(authored=authored) if authored else {}
    if legacy_ref:
        options["legacy_ref"] = legacy_ref
    return dict(
        collector=CURRENT,
        capture_sha256=capture_sha256,
        options=options,
        category=category,
        provenance=provenance or {},
    )


def convert(raw, legacy_ref, *, category, provenance, at, capture=None, authored=None):
    """Compile one retained capture; nothing is written and no live call is made."""
    asset = parse_capture(raw) if capture is None else capture
    request = dict(
        scope=scope(legacy_ref, asset["sha256"], category, provenance, authored), at=at
    )
    key = sha(canonical(request["scope"]).encode())[7:]
    options = dict(authored=authored) if authored else {}
    if legacy_ref:
        options["legacy_ref"] = legacy_ref
    package, tables, docs, record, _, report = compile_package(
        asset, options, request, key
    )
    return dict(
        package=package,
        tables=tables,
        docs=docs,
        report=report,
        request=request,
        key=key,
        record=record,
        capture=asset,
        raw=raw,
    )


def install(root, evidence, converted, *, roots=None):
    """Publish the package and keep replayable private evidence under ``evidence``."""
    if evidence is not None:
        retained_request(evidence, converted["request"], converted["key"])
        private_bytes(evidence, "captures", ".jsonl", converted["raw"])
        private_bytes(evidence, "evidence", ".validation.json", converted["report"])
    if roots is None:
        roots = {"private": str(Path(evidence).resolve())} if evidence is not None else {}
    return publish(
        Path(root),
        converted["package"],
        converted["tables"],
        {"main": converted["docs"]},
        roots=roots,
    )


def retained_request(evidence, request, key):
    """Write ``evidence/<key>.request.json`` with the pinned import instant.

    The live collector stamps the wall clock here; a migration must keep the one
    instant it compiled with, because the retained request is replayed verbatim.
    """
    path = relative(evidence, "evidence/" + key + ".request.json")
    Path(evidence).mkdir(parents=True, exist_ok=True)
    with (Path(evidence) / ".request.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            publish_bytes(path, canonical(request).encode())
            sync_directory(path.parent)
        stored = json.loads(path.read_bytes())
    if stored != request:
        fail("immutable collection request conflict")
    return path


def capabilities(converted):
    return {
        name: row["result"]
        for name, row in converted["docs"]["qualification"]["checks"].items()
    }


def template_ref(converted):
    return converted["tables"]["template"]["items"]["main"]["template_ref"]
