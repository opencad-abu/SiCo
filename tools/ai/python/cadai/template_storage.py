"""Read-only tier traversal, immutable reference conflict checks, and origin metadata."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .template_capture import json_value
from .template_locations import TemplateLocation, template_locations, template_root
from .template_schema import (
    MAX_RESPONSE_BYTES,
    SCHEMA_V3,
    SCHEMAS,
    TemplateError,
    TemplateUnavailable,
    canonical,
    digest,
)
from .template_snapshot import build_snapshot, verify_member
from .template_topology import evidence_gaps

MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 10000


def catalog_json(raw):
    try:
        return json_value(raw)
    except (TemplateError, TypeError, ValueError) as exc:
        raise TemplateUnavailable("invalid catalog JSON: " + str(exc)) from exc


def catalog_summary(raw, reference):
    summary = catalog_json(raw)
    if not isinstance(summary, dict) or summary.get("template_ref") != reference:
        raise TemplateUnavailable("catalog summary reference differs from index")
    if (
        any(not isinstance(summary.get(k), str) for k in ("library", "cell", "category"))
        or not isinstance(summary.get("assets"), list)
        or not isinstance(summary.get("counts"), dict)
    ):
        raise TemplateUnavailable("invalid catalog summary structure")
    return summary


class TemplateStorage:
    def __init__(self, root=None, *, workspace=None, cache=None):
        self._workspace = workspace
        self._explicit_root = root is not None
        self.root = Path(root).resolve() if root is not None else template_root(workspace)
        if cache is None and root is None:
            from .template_cache import cache_for_root

            cache = cache_for_root(self.root)
        self._cache = cache
        self._cache_event = "disabled" if cache is None or not cache.enabled else "miss"
        self.locations = (
            [TemplateLocation("private", self.root)]
            if root is not None
            else template_locations(workspace)
        )

    def _catalog_entries(self):
        """Read each physical path once, retaining every configured logical source."""
        result, directories, scanned = {}, {}, 0
        configured_roots = tuple(location.path.resolve() for location in self.locations)

        def allowed_alias(path):
            """Keep catalog aliases restricted to configured package data slots."""
            if not path.is_symlink():
                return True
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise TemplateUnavailable("catalog alias target unavailable: " + str(path)) from exc
            allowed = any(
                target in {root / "catalogs", root / "catalog.sqlite3"}
                or (target.parent == root / "catalogs" and target.suffix == ".sqlite3")
                for root in configured_roots
            )
            if not allowed:
                raise TemplateUnavailable(
                    "catalog alias escapes configured data slots: " + str(path)
                )
            return True

        def children(directory, names=None):
            nonlocal scanned
            directory = directory.resolve()
            key = (directory, frozenset(names) if names else None)
            if key not in directories:
                selected = []
                # Streaming traversal bounds memory and exposes permission/mount failures.
                with os.scandir(directory) as items:
                    for item in items:
                        scanned += 1
                        if scanned > MAX_DIRECTORY_ENTRIES:
                            raise TemplateUnavailable("template directory scan budget exceeded")
                        wanted = item.name in names if names else item.name.endswith(".sqlite3")
                        if wanted:
                            selected.append(Path(item.path))
                directories[key] = sorted(selected)
            return directories[key]

        for location in self.locations:
            root = location.path
            if not root.exists():
                continue
            if not root.is_dir():
                raise TemplateUnavailable("template root is not a directory: " + str(root))
            selected = {p.name: p for p in children(root, {"catalog.sqlite3", "catalogs"})}
            paths = [selected["catalog.sqlite3"]] if "catalog.sqlite3" in selected else []
            shards = selected.get("catalogs")
            if shards is not None:
                allowed_alias(shards)
                paths.extend(children(shards))
            for path in paths:
                allowed_alias(path)
                path = path.resolve()
                if not path.is_file():
                    raise TemplateUnavailable("catalog is not a readable file: " + str(path))
                locations = result.setdefault(path, [])
                if location not in locations:
                    locations.append(location)
                if len(result) > 256:
                    raise TemplateUnavailable(
                        "More than 256 catalog shards; compact the catalog offline"
                    )
        return list(result.items())

    def _entries(self):
        """Observe the current directory package contents for one read."""

        raw = self._catalog_entries()
        catalog_paths = {}
        for path, locations in raw:
            for location in locations:
                paths = catalog_paths.setdefault(location.path.resolve(), [])
                if path not in paths:
                    paths.append(path)
        snapshot = build_snapshot(self.locations, catalog_paths)
        self._last_snapshot = snapshot
        self._snapshot_members = {
            Path(path).resolve(): dict(member)
            for library in snapshot.libraries
            for member in library["members"]
            for path in [member["path"]]
        }
        return list(snapshot.entries)

    def _verify_snapshot(self):
        """Reject membership or content changes before returning a read result."""
        snapshot = getattr(self, "_last_snapshot", None)
        if snapshot is None:
            return
        if self._catalog_entries() != list(snapshot.entries):
            raise TemplateUnavailable("catalog membership changed during read; retry")
        for member in self._snapshot_members.values():
            verify_member(member, content=True)

    def index_path(self):
        """Return the private sidecar path for the currently fixed snapshot.

        The reader never creates or updates this file.  A maintenance process
        may build it with :func:`template_index.build_index`; shared catalog
        roots therefore remain read-only and an absent sidecar simply means a
        complete search.
        """

        if self._explicit_root:
            return None
        snapshot = getattr(self, "_last_snapshot", None)
        if snapshot is None:
            return None
        return self.root / ".indexes" / (snapshot.snapshot_ref + ".json")

    def index_candidates(self, records, target, mode, *, fixed_ref=None):
        """Use a validated necessary-condition sidecar without replacing matching.

        The current records are checked against the sidecar before filtering.
        Any missing row, digest/fact mismatch, incompatible version or corrupt
        file falls back to the complete pool and reports ``index_fallback``.
        """

        if fixed_ref is not None:
            return records, "disabled"
        path = self.index_path()
        if path is None or not path.exists():
            return records, "disabled"
        try:
            if evidence_gaps(target):
                return records, "index_fallback"
        except (KeyError, TypeError, ValueError, TemplateError):
            return records, "index_fallback"
        from .template_index import (
            INDEX_RULE_VERSION,
            MATCHER_VERSION,
            NORMALIZER_VERSION,
            load_index,
            necessary_candidates,
            validate_index_records,
        )

        index, reason = load_index(
            path,
            self._last_snapshot.snapshot_ref,
            matcher_version=MATCHER_VERSION,
            normalizer_version=NORMALIZER_VERSION,
            index_rule_version=INDEX_RULE_VERSION,
        )
        if index is None:
            return records, reason or "index_fallback"
        if not validate_index_records(index, list(records.values())):
            return records, "index_fallback"
        from collections import Counter

        kinds = Counter(device.get("kind") for device in target.get("devices", []))
        try:
            if mode == "exact":
                refs = necessary_candidates(
                    index,
                    device_kinds=kinds,
                    min_devices=len(target.get("devices", [])),
                    max_devices=len(target.get("devices", [])),
                )
            elif mode == "core":
                refs = necessary_candidates(
                    index,
                    available_device_kinds=kinds,
                    max_devices=len(target.get("devices", [])),
                    count_field="required_device_count",
                    kind_field="required_device_kinds",
                )
            else:
                return records, "disabled"
        except (KeyError, TypeError, ValueError, TemplateError):
            return records, "index_fallback"
        # A count mismatch is only a proof when the indexed record's
        # electrical evidence is complete.  Legacy captures with unknown
        # terminal inventory, bus scope, or device class must remain in the
        # pool so the authoritative matcher can return ``inconclusive``.
        uncertain = set()
        for reference, record in records.items():
            try:
                if evidence_gaps(record.get("topology", {})):
                    uncertain.add(reference)
            except (KeyError, TypeError, ValueError, TemplateError):
                uncertain.add(reference)
        selected = {
            reference: records[reference]
            for reference in set(refs) | uncertain
            if reference in records
        }
        if not selected and records:
            # The current public core matcher uses ``no_catalog_data`` for an
            # empty pool.  Keep the complete pool in this case so enabling an
            # index cannot change that envelope into an unavailable result;
            # a future matcher version may expose a dedicated proven no-match
            # branch and consume an empty necessary-condition set directly.
            return records, "index_fallback"
        return selected, "used"

    def _paths(self):
        return [path for path, _ in self._entries()]

    def source_report(self, entries):
        return [
            {
                **loc.describe(),
                "catalog_count": sum(loc in sources for _, sources in entries),
                "status": "missing"
                if not loc.path.exists()
                else "ready"
                if any(loc in sources for _, sources in entries)
                else "empty",
            }
            for loc in self.locations
        ]

    @staticmethod
    def bounded(result):
        if len(canonical(result).encode()) > MAX_RESPONSE_BYTES:
            raise TemplateUnavailable("template response exceeds budget; narrow query or page")
        return result

    @contextmanager
    def _connection(self, path):
        member = getattr(self, "_snapshot_members", {}).get(Path(path).resolve())
        if member is not None:
            verify_member(member)
        db = self._connect(path)
        try:
            yield db
            if member is not None:
                verify_member(member)
        finally:
            db.close()

    def _connect(self, path):
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(lambda: 1, 5000000)
            stored = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            if stored is None or stored[0] not in SCHEMAS:
                raise TemplateUnavailable("unsupported template catalog schema: " + str(path))
        except BaseException:
            db.close()
            raise
        return db

    def _read(self, path, reference, *, max_bytes=MAX_RECORD_BYTES):
        # Content hits are scoped by the current member digest, not merely the
        # immutable ref.  Every caller obtains a new snapshot before this
        # method, so a changed catalog can never be hidden by a hit.
        snapshot_ref = getattr(getattr(self, "_last_snapshot", None), "snapshot_ref", None)
        member = getattr(self, "_snapshot_members", {}).get(Path(path).resolve())
        member_digest = member.get("sha256") if member is not None else None
        if self._cache is not None and snapshot_ref is not None and member_digest is not None:
            from .template_cache import READER_SCHEMA_VERSION

            cached = self._cache.content_get(
                snapshot_ref,
                reference,
                member_digest,
                reader_schema_version=READER_SCHEMA_VERSION,
            )
            self._cache_event = self._cache.event("content")
            if cached is not None:
                if len(canonical(cached).encode()) > max_bytes:
                    raise TemplateUnavailable(
                        "template reference verification byte budget exceeded"
                    )
                # The SQLite summary is an independent searchable index.  A
                # content hit may skip JSON decoding, but it must still prove
                # that the current row's derived summary agrees with the
                # immutable record before any caller consumes the value.
                with self._connection(path) as db:
                    summary_row = db.execute(
                        "SELECT length(CAST(summary_json AS BLOB)), summary_json "
                        "FROM templates WHERE ref=?", (reference,)
                    ).fetchone()
                if summary_row is None:
                    raise TemplateUnavailable(
                        "catalog reference disappeared during cached read: " + reference
                    )
                if summary_row[0] is None or summary_row[0] > 32 * 1024 * 1024:
                    raise TemplateUnavailable("catalog summary exceeds query byte budget")
                summary = catalog_summary(summary_row[1], reference)
                if summary != cached.get("summary"):
                    raise TemplateUnavailable(
                        "catalog summary differs from stored record: " + reference
                    )
                return cached
        with self._connection(path) as db:
            row = db.execute(
                "SELECT length(CAST(data_json AS BLOB)) FROM templates WHERE ref=?", (reference,)
            ).fetchone()
            if row is None:
                return None
            if row[0] is None:
                raise TemplateUnavailable("catalog record is null")
            if row[0] > MAX_RECORD_BYTES:
                raise TemplateUnavailable("template record exceeds 64 MiB")
            if row[0] > max_bytes:
                raise TemplateUnavailable("template reference verification byte budget exceeded")
            record = catalog_json(
                db.execute("SELECT data_json FROM templates WHERE ref=?", (reference,)).fetchone()[
                    0
                ]
            )
            if not isinstance(record, dict) or record.get("template_ref") != reference:
                raise TemplateUnavailable("catalog reference differs from stored record")
            if (
                record.get("schema_version") not in SCHEMAS
                or any(
                    not isinstance(record.get(k), dict)
                    for k in ("source", "provenance", "assets", "summary")
                )
                or not isinstance(record.get("missing_assets"), list)
                or any(not isinstance(asset, dict) for asset in record["assets"].values())
                or ("topology" in record and not isinstance(record["topology"], dict))
            ):
                raise TemplateUnavailable("invalid catalog record structure")
            if record["summary"].get("template_ref") != reference:
                raise TemplateUnavailable("catalog record summary reference differs from index")
            if record["schema_version"] == SCHEMA_V3:
                from .template_reuse_schema import validate_record

                try:
                    validate_record(record)
                except TemplateError as exc:
                    raise TemplateUnavailable("invalid template v3: " + str(exc)) from exc
            if self._cache is not None and snapshot_ref is not None and member_digest is not None:
                self._cache_event = self._cache.content_put(
                    snapshot_ref, reference, record, digest(record), member_digest,
                    reader_schema_version=READER_SCHEMA_VERSION,
                )
            return record

    def locate(self, reference):
        record, origins, encoded, scanned_bytes = None, [], None, 0
        for path, locations in self._entries():
            current = self._read(path, reference, max_bytes=128 * 1024 * 1024 - scanned_bytes)
            if current is None:
                continue
            content = canonical(current)
            scanned_bytes += len(content.encode())
            if scanned_bytes > 128 * 1024 * 1024:
                raise TemplateUnavailable("template reference verification exceeds 128 MiB")
            if encoded is not None and content != encoded:
                raise TemplateUnavailable("immutable template reference conflict: " + reference)
            if record is None:
                record, encoded = current, content
            origins.extend({**loc.describe(), "catalog": str(path)} for loc in locations)
        if record is None:
            raise TemplateUnavailable(
                "template version not found in configured libraries: " + reference
            )
        self._verify_snapshot()
        origins = self.order_origins(origins)
        return record, {"catalog_origin": origins[0], "available_from": origins}

    def order_origins(self, origins):
        order = {(loc.tier, str(loc.path), loc.legacy): i for i, loc in enumerate(self.locations)}
        return sorted(
            origins,
            key=lambda o: (
                order[(o["tier"], o["root"], o["legacy"])],
                o["catalog"],
            ),
        )

    def get(self, reference):
        return self.locate(reference)[0]

    def ensure_private_destination(self):
        destination = self.root.resolve()
        for loc in self.locations:
            if loc.tier not in {"builtin", "project"}:
                continue
            protected = [
                loc.path,
                *((loc.path / name).resolve() for name in ("catalogs", "captures", "previews")),
            ]
            writes = [
                destination,
                *((destination / name).resolve() for name in ("catalogs", "captures", "previews")),
            ]
            if any(
                target == source or source in target.parents
                for target in writes
                for source in protected[1:]
            ) or any(target == loc.path for target in writes):
                raise TemplateUnavailable(
                    "private template destination overlaps a read-only shared library"
                )
        if not self._explicit_root:
            current = template_root(self.root.parents[2], create=True)
            if current != self.root:
                raise TemplateUnavailable("project state root changed; reopen the template library")
        return self.root
