"""Bounded offline catalog validation returning only immutable content references."""

import hashlib
import re
import sqlite3

from .template_schema import REF_PATTERN, TemplateUnavailable, canonical
from .template_storage import MAX_RECORD_BYTES, TemplateStorage, catalog_summary

MAX_CATALOG_ROWS = 10000
MAX_CATALOG_JSON = 128 * 1024 * 1024


def validate_structure(db):
    tables = {"metadata": ("key", "value"), "templates": (
        "ref", "library", "cell", "category", "device_count", "fingerprint",
        "search_text", "summary_json", "data_json")}
    indexes = {"sqlite_autoindex_metadata_1": ("metadata", ("key",)),
               "sqlite_autoindex_templates_1": ("templates", ("ref",)),
               "lookup": ("templates", ("library", "cell", "category", "device_count")),
               "topology_lookup": ("templates", ("fingerprint",))}
    objects = db.execute("SELECT type,name,tbl_name FROM sqlite_master").fetchall()
    for kind, name, table in objects:
        if kind == "table" and name in tables and table == name:
            continue
        if kind != "index" or name not in indexes or table != indexes[name][0]:
            raise ValueError("Unexpected template database object")
        columns = tuple(row[2] for row in db.execute('PRAGMA index_info("' + name + '")'))
        if columns != indexes[name][1]:
            raise ValueError("Unexpected template database index")
    for table, columns in tables.items():
        info = db.execute('PRAGMA table_xinfo("' + table + '")').fetchall()
        if (tuple(row[1] for row in info) != columns
                or any(row[6] or row[4] is not None for row in info)
                or tuple(row[5] for row in info) != (1,) + (0,) * (len(columns) - 1)):
            raise ValueError("Unexpected template database columns or primary key")


def catalog_references(path):
    """Validate a stable private snapshot; callers retain ownership of the file."""
    storage = TemplateStorage(root=path.parent)
    try:
        db = storage._connect(path)
        try:
            return _references(storage, db, path)
        finally:
            db.close()
    except (sqlite3.Error, TemplateUnavailable) as exc:
        raise ValueError("Invalid template catalog") from exc


def _references(storage, db, path):
    validate_structure(db)
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Template catalog integrity check failed")
    sizes = db.execute("SELECT ref,length(CAST(data_json AS BLOB)),"
                       "length(CAST(summary_json AS BLOB)) FROM templates ORDER BY ref").fetchmany(
                           MAX_CATALOG_ROWS + 1)
    if not sizes or len(sizes) > MAX_CATALOG_ROWS:
        raise ValueError("Template catalog row limit")
    budget, result = 0, []
    for reference, size, summary_size in sizes:
        if (not isinstance(reference, str) or not re.fullmatch(REF_PATTERN, reference)
                or type(size) is not int or not 0 < size <= MAX_RECORD_BYTES
                or type(summary_size) is not int or not 0 < summary_size <= MAX_RECORD_BYTES):
            raise ValueError("Invalid template record bounds")
        budget += size + summary_size
        if budget > MAX_CATALOG_JSON:
            raise ValueError("Template catalog validation budget exceeded")
        record = storage._read(path, reference)
        raw_summary = db.execute("SELECT summary_json FROM templates WHERE ref=?",
                                 (reference,)).fetchone()[0]
        summary = catalog_summary(raw_summary, reference)
        if canonical(summary) != canonical(record["summary"]):
            raise ValueError("Template summary differs from stored record")
        capture_hash = record.get("capture_sha256")
        if not isinstance(capture_hash, str) or not re.fullmatch("[a-f0-9]{64}", capture_hash):
            raise ValueError("Missing template capture identity")
        result.append({"template_ref": reference, "capture_sha256": capture_hash,
                       "sha256": hashlib.sha256(canonical(record).encode()).hexdigest()})
    return result
