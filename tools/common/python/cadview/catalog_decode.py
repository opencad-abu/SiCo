"""Validate and decode the catalog JSON protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
from .catalog_errors import CatalogError
from .catalog_model import (CATALOG_SCHEMA_VERSION, Catalog, CatalogLibrary, CatalogCategory, CatalogCombineGroup, CatalogCell, CatalogView)


def decode_catalog(
    payload: Mapping[str, Any],
    *,
    cds_library_file: str | Path | None = None,
    authoritative: bool | None = None,
    provider: str | None = None,
    catalog_type=Catalog,
) -> "Catalog":
    """Decode the dbAccess JSON protocol and validate its shape.

    The decoder accepts ``read_path`` as well as the compact ``path`` key,
    and accepts view names as strings.  The latter keeps the protocol easy
    to emit from SKILL while retaining paths when the provider supplies
    them.
    """

    if not isinstance(payload, Mapping):
        raise CatalogError("catalog JSON root must be an object")
    raw_version = payload.get("schema_version", payload.get("version", 0))
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise CatalogError("catalog schema_version must be an integer") from exc
    if version != CATALOG_SCHEMA_VERSION:
        raise CatalogError(f"unsupported catalog schema version: {version}")

    raw_libraries = payload.get("libraries", ())
    if not isinstance(raw_libraries, Sequence) or isinstance(
        raw_libraries, (str, bytes, bytearray)
    ):
        raise CatalogError("catalog libraries must be an array")

    base = (
        Path(cds_library_file).expanduser().resolve()
        if cds_library_file is not None
        else _required_path(payload, "cds_library_file")
    )
    libraries = tuple(
        sorted(
            (_library_from_dict(item, base.parent) for item in raw_libraries),
            key=lambda item: item.name,
        )
    )
    groups_were_supplied = "combine_groups" in payload
    raw_groups = payload.get("combine_groups", ())
    if not isinstance(raw_groups, Sequence) or isinstance(
        raw_groups, (str, bytes, bytearray)
    ):
        raise CatalogError("catalog combine_groups must be an array")
    groups = tuple(
        sorted(
            (_combine_group_from_dict(item) for item in raw_groups),
            key=lambda item: item.name,
        )
    )
    # Older/custom providers may expose the relationship only on each
    # library.  Promote that representation to the catalog-level index so
    # GUI consumers have one stable source for constructing a tree.
    if not groups and not groups_were_supplied:
        groups = tuple(
            CatalogCombineGroup(library.name, library.combine_members)
            for library in libraries
            if library.combine_members
        )
    return catalog_type(
        cds_library_file=base,
        libraries=libraries,
        authoritative=(
            bool(payload.get("authoritative", False))
            if authoritative is None
            else authoritative
        ),
        provider=str(payload.get("provider", "dbAccess") if provider is None else provider),
        schema_version=version,
        combine_groups=groups,
    )


def _required_path(payload: Mapping[str, Any], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"catalog JSON requires {key}")
    return Path(value).expanduser().resolve()


def _library_from_dict(raw: Any, base: Path) -> CatalogLibrary:
    if not isinstance(raw, Mapping):
        raise CatalogError("catalog library entries must be objects")
    name = _required_name(raw, "name", "library")
    path = _optional_path(raw, "read_path", raw.get("path"), base)
    if path is None:
        raise CatalogError(f"catalog library {name!r} requires path")
    write_path = _optional_path(raw, "write_path", None, base)
    raw_cells = raw.get("cells", ())
    if not isinstance(raw_cells, Sequence) or isinstance(
        raw_cells, (str, bytes, bytearray)
    ):
        raise CatalogError(f"catalog library {name!r} cells must be an array")
    cells = tuple(
        sorted(
            (_cell_from_dict(item, path) for item in raw_cells),
            key=lambda item: item.name,
        )
    )
    raw_combine_members = raw.get("combine_members", ())
    if not isinstance(raw_combine_members, Sequence) or isinstance(
        raw_combine_members, (str, bytes, bytearray)
    ):
        raise CatalogError(
            f"catalog library {name!r} combine_members must be an array"
        )
    combine_members = tuple(
        _required_member_name(item, f"catalog library {name!r} combine member")
        for item in raw_combine_members
    )
    raw_categories = raw.get("categories", ())
    if not isinstance(raw_categories, Sequence) or isinstance(
        raw_categories, (str, bytes, bytearray)
    ):
        raise CatalogError(
            f"catalog library {name!r} categories must be an array"
        )
    categories = tuple(
        sorted(
            (_category_from_dict(item) for item in raw_categories),
            key=lambda item: item.name,
        )
    )
    writable = bool(raw.get("writable", write_path is not None))
    if writable and write_path is None and "write_path" not in raw:
        write_path = path
    return CatalogLibrary(
        name,
        path,
        writable,
        cells,
        write_path,
        combine_members,
        categories,
    )


def _required_member_name(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{kind} must be a non-empty string")
    if "/" in value or "\\" in value or "\x00" in value:
        raise CatalogError(f"{kind} has unsafe name: {value!r}")
    return value


def _category_from_dict(raw: Any) -> CatalogCategory:
    if not isinstance(raw, Mapping):
        raise CatalogError("catalog category entries must be objects")
    name = _required_member_name(raw.get("name"), "catalog category name")
    raw_members = raw.get("members", ())
    if not isinstance(raw_members, Sequence) or isinstance(
        raw_members, (str, bytes, bytearray)
    ):
        raise CatalogError(f"catalog category {name!r} members must be an array")
    members = tuple(
        sorted(
            {
                _required_member_name(
                    item, f"catalog category {name!r} member"
                )
                for item in raw_members
            }
        )
    )
    raw_children = raw.get("children", ())
    if not isinstance(raw_children, Sequence) or isinstance(
        raw_children, (str, bytes, bytearray)
    ):
        raise CatalogError(f"catalog category {name!r} children must be an array")
    children = tuple(
        sorted(
            (_category_from_dict(item) for item in raw_children),
            key=lambda item: item.name,
        )
    )
    return CatalogCategory(name, members, children)


def _combine_group_from_dict(raw: Any) -> CatalogCombineGroup:
    if not isinstance(raw, Mapping):
        raise CatalogError("catalog combine group entries must be objects")
    name = _required_member_name(raw.get("name"), "catalog combine group name")
    raw_members = raw.get("members", ())
    if not isinstance(raw_members, Sequence) or isinstance(
        raw_members, (str, bytes, bytearray)
    ):
        raise CatalogError(f"catalog combine group {name!r} members must be an array")
    members = tuple(
        _required_member_name(
            item, f"catalog combine group {name!r} member"
        )
        for item in raw_members
    )
    return CatalogCombineGroup(name, members)


def _cell_from_dict(raw: Any, base: Path) -> CatalogCell:
    if not isinstance(raw, Mapping):
        raise CatalogError("catalog cell entries must be objects")
    name = _required_name(raw, "name", "cell")
    path = _optional_path(raw, "path", None, base / name)
    if path is None:
        path = (base / name).resolve()
    raw_views = raw.get("views", ())
    if not isinstance(raw_views, Sequence) or isinstance(
        raw_views, (str, bytes, bytearray)
    ):
        raise CatalogError(f"catalog cell {name!r} views must be an array")
    views: list[CatalogView] = []
    for item in raw_views:
        if isinstance(item, str):
            view_name = item
            view_path = path / item
        elif isinstance(item, Mapping):
            view_name = _required_name(item, "name", "view")
            view_path = _optional_path(item, "path", None, path / view_name)
            if view_path is None:
                view_path = (path / view_name).resolve()
        else:
            raise CatalogError(f"catalog cell {name!r} has invalid view entry")
        views.append(CatalogView(view_name, view_path))
    return CatalogCell(name, path, tuple(sorted(views, key=lambda item: item.name)))


def _required_name(raw: Mapping[str, Any], key: str, kind: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"catalog {kind} requires non-empty {key}")
    if "/" in value or "\\" in value or "\x00" in value:
        raise CatalogError(f"catalog {kind} has unsafe name: {value!r}")
    return value


def _optional_path(
    raw: Mapping[str, Any],
    key: str,
    fallback: Any,
    base: Path,
) -> Path | None:
    value = raw.get(key, fallback)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"catalog path {key} must be a string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()
