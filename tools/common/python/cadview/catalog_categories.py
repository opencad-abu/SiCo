"""Read bounded library-local .TopCat/.Cat category hierarchies."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Sequence
from .catalog_model import CatalogCategory, CatalogCell


_CATEGORY_RECORD = re.compile(r'^\s*(?P<qualified>[^\s]+)\s+type="(?P<kind>[^"]+)"')


def _discover_categories(
    library_path: Path,
    library_name: str,
    cells: Sequence[CatalogCell],
) -> tuple[CatalogCategory, ...]:
    """Read legacy ``.TopCat``/``.Cat`` metadata when available.

    Some detached ``dbAccess`` runtimes expose ``ddGetCombineValue`` but not
    the ``ddCat*`` family.  Category files are the durable on-disk format
    used by Library Manager, so parsing them here keeps the source provider
    useful across Cadence releases.  Malformed or stale category records are
    ignored; they must not make an otherwise valid catalog unavailable.
    """

    try:
        library_root = library_path.resolve()
        topcat = _safe_category_path(
            library_root, library_root / f"{library_name}.TopCat"
        )
    except (OSError, RuntimeError):
        return ()
    if topcat is None or not topcat.is_file():
        return ()
    cell_names = {cell.name for cell in cells}
    result: list[CatalogCategory] = []
    seen_top_categories: set[str] = set()
    try:
        records = _read_category_records(topcat)
    except OSError:
        return ()
    for qualified, kind in records:
        if kind != "category":
            continue
        member_name = _category_member_name(qualified, library_name)
        if member_name is None or member_name in seen_top_categories:
            continue
        seen_top_categories.add(member_name)
        category_file = library_root / member_name
        category = _read_category_file(
            category_file,
            Path(member_name).stem,
            library_name,
            cell_names,
            library_root=library_root,
            visiting=set(),
        )
        if category is not None:
            result.append(category)
    return tuple(sorted(result, key=lambda item: item.name))


def _read_category_file(
    category_file: Path,
    category_name: str,
    library_name: str,
    cell_names: set[str],
    *,
    library_root: Path,
    visiting: set[Path],
) -> CatalogCategory | None:
    try:
        category_file = _safe_category_path(library_root, category_file)
    except (OSError, RuntimeError):
        return None
    if category_file is None:
        return None
    if category_file in visiting or not category_file.is_file():
        return None
    visiting = {*visiting, category_file}
    try:
        records = _read_category_records(category_file)
    except OSError:
        return None
    members: set[str] = set()
    children: list[CatalogCategory] = []
    seen_child_categories: set[str] = set()
    for qualified, kind in records:
        member_name = _category_member_name(qualified, library_name)
        if member_name is None:
            continue
        if kind == "cell":
            if member_name in cell_names:
                members.add(member_name)
        elif kind == "category":
            if member_name in seen_child_categories:
                continue
            seen_child_categories.add(member_name)
            child = _read_category_file(
                library_root / member_name,
                Path(member_name).stem,
                library_name,
                cell_names,
                library_root=library_root,
                visiting=visiting,
            )
            if child is not None:
                children.append(child)
    return CatalogCategory(
        category_name,
        tuple(sorted(members)),
        tuple(sorted(children, key=lambda item: item.name)),
    )


def _safe_category_path(library_root: Path, candidate: Path) -> Path | None:
    """Resolve one category file without following it outside the library."""

    resolved = candidate.resolve()
    try:
        resolved.relative_to(library_root)
    except ValueError:
        return None
    return resolved


def _category_member_name(qualified: str, library_name: str) -> str | None:
    """Return a safe local category/cell name with the expected library prefix.

    Library Manager category records are qualified as ``library/name``.  We
    intentionally require that exact prefix before resolving a local file or
    accepting a cell member.  Without this check, a stale entry from another
    library could accidentally select a same-named local category or cell.
    Names are single path components in the DD category format; rejecting
    absolute, parent-traversal, and nested paths also keeps the fallback from
    escaping the physical library directory.
    """

    if not isinstance(qualified, str) or not isinstance(library_name, str):
        return None
    prefix = library_name + "/"
    if not qualified.startswith(prefix):
        return None
    member_name = qualified[len(prefix) :]
    if (
        not member_name
        or member_name in {".", ".."}
        or "/" in member_name
        or "\\" in member_name
        or "\x00" in member_name
        or Path(member_name).is_absolute()
    ):
        return None
    return member_name


def _read_category_records(path: Path) -> tuple[tuple[str, str], ...]:
    records: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _CATEGORY_RECORD.match(raw_line)
        if match:
            records.append((match.group("qualified"), match.group("kind").lower()))
    return tuple(records)
