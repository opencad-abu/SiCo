"""Best-effort metadata enrichment for provider catalogs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence

from .catalog_model import Catalog, CatalogCategory, CatalogCombineGroup, CatalogLibrary
from .catalog_categories import _discover_categories
from .cdslib import read_library_definitions
from .errors import Nl2ViewError

def _augment_category_metadata(catalog: Catalog) -> Catalog:
    """Fill category metadata from on-disk files when SKILL lacks ``ddCat*``.

    ``dbAccess`` releases used by some PDKs expose the DD library APIs but not
    the newer category API.  The provider protocol therefore emits an empty
    category array in that case.  Reading the library's ``.TopCat`` and
    ``.Cat`` files is deterministic and does not cross into the target
    Virtuoso session.
    """

    changed = False
    libraries: list[CatalogLibrary] = []
    for library in catalog.libraries:
        disk_categories = _discover_categories(
            library.read_path, library.name, library.cells
        )
        categories = _merge_categories(library.categories, disk_categories)
        if categories != library.categories:
            library = replace(library, categories=categories)
            changed = True
        libraries.append(library)
    if not changed:
        return catalog
    return replace(catalog, libraries=tuple(libraries))

def _merge_categories(
    primary: Sequence[CatalogCategory],
    fallback: Sequence[CatalogCategory],
) -> tuple[CatalogCategory, ...]:
    """Fill missing API category branches from durable library metadata.

    A detached runtime can expose ``ddCat*`` but fail to open one Category.
    The SKILL protocol deliberately skips that node so its JSON remains
    valid.  Merge by name at every level: API members remain authoritative,
    while a missing root or child is restored from ``.TopCat/.Cat``.
    """

    fallback_by_name = {category.name: category for category in fallback}
    merged: dict[str, CatalogCategory] = {}
    for category in primary:
        disk_category = fallback_by_name.pop(category.name, None)
        if disk_category is not None:
            children = _merge_categories(
                category.children,
                disk_category.children,
            )
            if children != category.children:
                category = replace(category, children=children)
        merged[category.name] = category
    merged.update(fallback_by_name)
    return tuple(sorted(merged.values(), key=lambda item: item.name))

def _augment_combine_metadata(
    catalog: Catalog,
    cds_library_file: Path,
    environ: Mapping[str, str],
) -> Catalog:
    """Best-effort COMBINE fallback for runtimes without DD support.

    ``ddGetCombineValue`` is present in current Cadence releases, but older
    detached dbAccess executables can omit it.  Parsing only the definitions
    and ASSIGN statements is inexpensive; unsupported cds.lib constructs are
    allowed to leave the authoritative DD result unchanged.
    """

    try:
        parsed = read_library_definitions(
            cds_library_file,
            environ=environ,
        )
    except (Nl2ViewError, OSError, RuntimeError):
        return catalog

    groups_by_name = {group.name: group for group in catalog.combine_groups}
    catalog_library_names = {library.name for library in catalog.libraries}
    for name, members in parsed.combine_groups.items():
        if name in catalog_library_names and name not in groups_by_name:
            groups_by_name[name] = CatalogCombineGroup(name, members)
    groups = tuple(sorted(groups_by_name.values(), key=lambda item: item.name))
    member_map = {group.name: group.members for group in groups}
    libraries = tuple(
        replace(library, combine_members=member_map[library.name])
        if library.name in member_map
        and library.combine_members != member_map[library.name]
        else library
        for library in catalog.libraries
    )
    if groups == catalog.combine_groups and libraries == catalog.libraries:
        return catalog
    return replace(catalog, libraries=libraries, combine_groups=groups)
