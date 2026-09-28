"""Pure COMBINE graph construction with cycle guards and presentation filtering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union
from cadview.catalog import Catalog, CatalogLibrary
from .library_filter import matches


@dataclass(frozen=True)
class _LibraryLeaf:
    library: CatalogLibrary


@dataclass(frozen=True)
class _LibraryGroup:
    name: str
    children: Tuple["_LibraryNode", ...]
    libraries: Tuple[CatalogLibrary, ...] = ()


_LibraryNode = Union[_LibraryLeaf, _LibraryGroup]


def library_nodes(catalog: Optional[Catalog], filter_text: str) -> Tuple[_LibraryNode, ...]:
    """Build the physical-library rows and optional COMBINE tree roots."""

    if catalog is None:
        return ()
    libraries = {library.name: library for library in catalog.libraries}
    raw_groups = getattr(catalog, "combine_groups", ()) or ()
    if not raw_groups:
        # Accept catalogs emitted by older/custom providers that attach
        # COMBINE members directly to the virtual library object.
        raw_groups = tuple(
            (library.name, library.combine_members)
            for library in catalog.libraries
            if getattr(library, "combine_members", ())
        )

    definitions: dict[str, Tuple[str, ...]] = {}
    definition_order: list[str] = []
    for raw_group in raw_groups:
        if isinstance(raw_group, tuple):
            name, raw_members = raw_group
        else:
            name = getattr(raw_group, "name", None)
            raw_members = getattr(raw_group, "members", ())
        if not isinstance(name, str) or not name:
            continue
        members = tuple(str(member) for member in (raw_members or ()))
        if name not in definitions:
            definition_order.append(name)
        # Match cds.lib's sequential assignment behavior: the last
        # definition of the same virtual library replaces the earlier one.
        definitions[name] = members

    referenced_groups = {
        member
        for members in definitions.values()
        for member in members
        if member in definitions
    }
    root_names = [
        name for name in definition_order if name not in referenced_groups
    ]
    # A malformed cycle has no natural root.  Rendering every definition
    # with a recursion guard is more useful than silently losing the tree.
    if definitions and not root_names:
        root_names = list(definition_order)

    def build_group(name: str, stack: Tuple[str, ...]) -> _LibraryGroup:
        children: list[_LibraryNode] = []
        aggregate: list[CatalogLibrary] = []
        seen_libraries: set[str] = set()

        def include_library(library: Optional[CatalogLibrary]) -> None:
            if library is None or library.name in seen_libraries:
                return
            seen_libraries.add(library.name)
            aggregate.append(library)

        # A combined library can have physical data of its own.  Cadence
        # presents that data before the member-library contributions.
        include_library(libraries.get(name))
        seen_tokens: set[str] = set()
        for member in definitions.get(name, ()):
            if member in seen_tokens:
                continue
            seen_tokens.add(member)
            if member in definitions:
                if member not in stack:
                    child_group = build_group(member, (*stack, member))
                    children.append(child_group)
                    for library in child_group.libraries:
                        include_library(library)
                continue
            library = libraries.get(member)
            if library is not None and member not in definitions:
                children.append(_LibraryLeaf(library))
                include_library(library)
        return _LibraryGroup(name, tuple(children), tuple(aggregate))

    nodes: list[_LibraryNode] = [
        build_group(name, (name,)) for name in root_names
    ]
    referenced_physical = {
        member
        for members in definitions.values()
        for member in members
        if member in libraries and member not in definitions
    }
    nodes.extend(
        _LibraryLeaf(library)
        for library in catalog.libraries
        if library.name not in definitions
        and library.name not in referenced_physical
    )


    def filtered(node: _LibraryNode, include_all: bool = False):
        if isinstance(node, _LibraryLeaf):
            return node if include_all or matches(
                node.library.name, filter_text
            ) else None
        group_matches = include_all or matches(node.name, filter_text)
        children = tuple(
            child
            for child in (
                filtered(item, group_matches) for item in node.children
            )
            if child is not None
        )
        if group_matches or children:
            # Filtering changes only presentation.  The aggregate scope
            # remains the full COMBINE definition, including hidden
            # members and data physically stored in the top library.
            return _LibraryGroup(node.name, children, node.libraries)
        return None

    visible_nodes = tuple(
        node
        for node in (filtered(candidate) for candidate in nodes)
        if node is not None
    )

    return visible_nodes
