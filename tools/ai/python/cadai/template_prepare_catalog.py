"""Materialize one content-checked, conflict-checked catalog pool for preparation."""

import sqlite3

from .template_schema import TemplateUnavailable, canonical


def catalog_records(catalog, allowed_tiers):
    """Materialize one immutable, conflict-checked snapshot for matching."""
    records, origins, scanned, total_bytes = {}, {}, 0, 0
    try:
        entries = catalog._entries()
        selected_entries = [
            (path, locations)
            for path, locations in entries
            if any(loc.tier in allowed_tiers for loc in locations)
        ]
        other_entries = [
            (path, locations)
            for path, locations in entries
            if not any(loc.tier in allowed_tiers for loc in locations)
        ]
        for path, locations in selected_entries:
            with catalog._connection(path) as db:
                for (reference,) in db.execute("SELECT ref FROM templates ORDER BY ref"):
                    scanned += 1
                    if scanned > 10000:
                        raise TemplateUnavailable("template snapshot exceeds 10000 candidates")
                    record = catalog._read(path, reference, max_bytes=64 * 1024 * 1024)
                    if record is None:
                        continue
                    encoded = canonical(record)
                    if reference in records:
                        if canonical(records[reference]) != encoded:
                            raise TemplateUnavailable(
                                "immutable template reference conflict: " + reference
                            )
                    else:
                        total_bytes += len(encoded.encode())
                        if total_bytes > 32 * 1024 * 1024:
                            raise TemplateUnavailable("template snapshot exceeds 32 MiB")
                        records[reference] = record
                    origins.setdefault(reference, []).extend(
                        {**loc.describe(), "catalog": str(path)} for loc in locations
                    )
        # A tier filter controls which candidates are eligible, but it must not
        # hide an immutable-ref conflict with another configured tier.  Read
        # only duplicate refs from excluded catalogs; do not materialize their
        # unrelated records into this snapshot budget.
        references = sorted(records)
        for path, _locations in other_entries:
            with catalog._connection(path) as db:
                for offset in range(0, len(references), 500):
                    batch = references[offset : offset + 500]
                    if not batch:
                        continue
                    placeholders = ",".join("?" * len(batch))
                    rows = db.execute(
                        "SELECT ref FROM templates WHERE ref IN (" + placeholders + ")", batch
                    )
                    for (reference,) in rows:
                        current = catalog._read(path, reference, max_bytes=64 * 1024 * 1024)
                        if current is not None and canonical(current) != canonical(
                            records[reference]
                        ):
                            raise TemplateUnavailable(
                                "immutable template reference conflict: " + reference
                            )
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        raise TemplateUnavailable("template snapshot unavailable: " + str(exc)) from exc
    catalog._verify_snapshot()
    selected_records = {}
    selected_origins = {}
    for reference, rows in origins.items():
        unique = {(row["tier"], row["root"], row["legacy"], row["catalog"]): row for row in rows}
        normalized = sorted(unique.values(), key=lambda row: (row["tier"], row["catalog"]))
        selected = [row for row in normalized if row["tier"] in allowed_tiers]
        if selected:
            selected_records[reference] = records[reference]
            selected_origins[reference] = selected
    return selected_records, selected_origins
