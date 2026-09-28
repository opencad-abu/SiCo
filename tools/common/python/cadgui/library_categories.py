"""Pure category path merging, filtering and per-source membership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
from cadview.catalog import CatalogLibrary, CatalogCell
from .library_filter import matches


CategoryIdentity = Tuple[str, Tuple[str, ...]]
_CATEGORY_ALL: CategoryIdentity = ("all", ())
_CATEGORY_UNCATEGORIZED: CategoryIdentity = ("uncategorized", ())


@dataclass(frozen=True)
class _CategoryNode:
    identity: CategoryIdentity
    children: Tuple["_CategoryNode", ...]
    directly_matched: bool = True


@dataclass
class _MutableCategoryNode:
    name: str
    children: dict[str, "_MutableCategoryNode"]


def _is_category_identity(value) -> bool:
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and value[0] in {"all", "uncategorized", "category"}
        and isinstance(value[1], tuple)
        and all(isinstance(name, str) for name in value[1])
    )

def _category_legacy_name(identity: CategoryIdentity) -> str:
    kind, path = identity
    if kind == "all":
        return "Everything"
    if kind == "uncategorized":
        return "Uncategorized"
    return "/".join(path)

def _category_display(identity: CategoryIdentity) -> str:
    kind, path = identity
    if kind == "all":
        return "Everything"
    if kind == "uncategorized":
        return "Uncategorized"
    display = "/".join(path)
    if len(path) == 1 and display in {"Everything", "Uncategorized"}:
        return display + " (Category)"
    return display

def _category_item_label(identity: CategoryIdentity) -> str:
    kind, path = identity
    if kind == "all":
        return "Everything"
    if kind == "uncategorized":
        return "Uncategorized"
    label = path[-1] if path else ""
    if len(path) == 1 and label in {"Everything", "Uncategorized"}:
        return label + " (Category)"
    return label

def _category_rows(
        categories,
    parent: Tuple[str, ...] = (),
) -> list[tuple[CategoryIdentity, set[str]]]:
    rows: list[tuple[CategoryIdentity, set[str]]] = []
    for category in categories or ():
        name = str(getattr(category, "name", ""))
        if not name:
            continue
        path = (*parent, name)
        child_rows = _category_rows(
            getattr(category, "children", ()), path
        )
        members = set(getattr(category, "members", ()) or ())
        for _identity, child_members in child_rows:
            members.update(child_members)
        rows.append((("category", path), members))
        rows.extend(child_rows)
    return rows

def _merged_category_nodes(
        libraries: Tuple[CatalogLibrary, ...],
) -> Tuple[_CategoryNode, ...]:
    """Merge same-path Category branches across physical libraries."""

    roots: dict[str, _MutableCategoryNode] = {}

    def merge(categories, target: dict[str, _MutableCategoryNode]) -> None:
        for category in categories or ():
            name = str(getattr(category, "name", ""))
            if not name:
                continue
            node = target.get(name)
            if node is None:
                node = _MutableCategoryNode(name, {})
                target[name] = node
            merge(getattr(category, "children", ()), node.children)

    for library in libraries:
        merge(getattr(library, "categories", ()), roots)

    def freeze(
        nodes: dict[str, _MutableCategoryNode],
        parent: Tuple[str, ...] = (),
    ) -> Tuple[_CategoryNode, ...]:
        return tuple(
            _CategoryNode(
                ("category", (*parent, node.name)),
                freeze(node.children, (*parent, node.name)),
            )
            for node in nodes.values()
        )

    return freeze(roots)

def _filtered_category_node(
        node: _CategoryNode,
    filter_text: str,
    *,
    include_all: bool = False,
) -> Optional[_CategoryNode]:
    directly_matched = matches(
        _category_display(node.identity), filter_text
    )
    include_children = include_all or directly_matched
    children = tuple(
        child
        for child in (
            _filtered_category_node(
                candidate,
                filter_text,
                include_all=include_children,
            )
            for candidate in node.children
        )
        if child is not None
    )
    if include_children or children:
        return _CategoryNode(
            node.identity,
            children,
            directly_matched=directly_matched,
        )
    return None

def cell_matches_category(
    library: CatalogLibrary,
    cell: CatalogCell,
    category: Optional[CategoryIdentity],
    enabled: bool,
) -> bool:
    if not enabled:
        return True
    if category is None:
        return False
    if category == _CATEGORY_ALL:
        return True
    rows = dict(_category_rows(getattr(library, "categories", ())))
    if category == _CATEGORY_UNCATEGORIZED:
        categorized = set().union(*rows.values()) if rows else set()
        return cell.name not in categorized
    return cell.name in rows.get(category, set())
