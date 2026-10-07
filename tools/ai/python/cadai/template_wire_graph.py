"""Conservative physical wire connectivity; labels never bridge disconnected shapes."""

from collections import defaultdict

from .template_schema import TemplateError
from .template_terminal_contacts import shared_contacts


def on_segment(point, a, b):
    return (a[0] == b[0] == point[0] and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])) or (
        a[1] == b[1] == point[1] and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
    )


def crossing(a, b, c, d):
    if a[0] == b[0] and c[1] == d[1]:
        point = (a[0], c[1])
    elif a[1] == b[1] and c[0] == d[0]:
        point = (c[0], a[1])
    else:
        return None
    return point if on_segment(point, a, b) and on_segment(point, c, d) else None


def _collinear_overlap(a, b, c, d):
    """Return true for a positive length same-axis overlap, excluding a touch."""
    if a[1] == b[1] == c[1] == d[1]:
        return max(min(a[0], b[0]), min(c[0], d[0])) < min(max(a[0], b[0]), max(c[0], d[0]))
    if a[0] == b[0] == c[0] == d[0]:
        return max(min(a[1], b[1]), min(c[1], d[1])) < min(max(a[1], b[1]), max(c[1], d[1]))
    return False


def connect_net(segments, anchors, *, shared_terminals=False):
    """Split T junctions and terminal contacts; reject ambiguous interior X crossings."""
    if len(segments) > 512 or len(anchors) > 256:
        raise TemplateError("wire attachment net exceeds 512 segments / 256 terminals")
    gaps = set()
    points = {tuple(p) for s in segments for p in s["points"]}
    points.update(tuple(a["xy"]) for a in anchors)
    for index, left in enumerate(segments):
        a, b = left["points"]
        if a == b or (a[0] != b[0] and a[1] != b[1]):
            gaps.add("non_orthogonal_source_wire")
        for right in segments[index + 1 :]:
            c, d = right["points"]
            if _collinear_overlap(a, b, c, d):
                gaps.add("overlapping_source_wire")
            point = crossing(a, b, c, d)
            if point is not None and point not in {tuple(a), tuple(b), tuple(c), tuple(d)}:
                gaps.add("ambiguous_source_crossing")
    edges, graph = [], defaultdict(set)
    for segment in segments:
        a, b = segment["points"]
        members = sorted(p for p in points if on_segment(p, a, b))
        for start, end in zip(members, members[1:]):
            graph[start].add(end)
            graph[end].add(start)
            edges.append(
                {
                    "points": [list(start), list(end)],
                    "shape": segment["shape"],
                    "segment": segment["segment"],
                }
            )
    locations = {tuple(a["xy"]) for a in anchors}
    if not segments or not locations <= set(graph):
        gaps.add("source_endpoint_not_on_wire")
    contacts = shared_contacts(anchors) if shared_terminals else None
    if contacts is None and (shared_terminals or len(locations) != len(anchors)):
        gaps.add("ambiguous_coincident_source_terminals")
    if any(len(neighbors) == 1 and point not in locations for point, neighbors in graph.items()):
        gaps.add("open_source_wire_end")
    visited, todo = set(), [next(iter(graph))] if graph else []
    while todo:
        point = todo.pop()
        if point not in visited:
            visited.add(point)
            todo.extend(graph[point] - visited)
    if set(graph) - visited or locations - visited:
        gaps.add("source_net_disconnected")
    return {
        "edges": edges,
        "gaps": sorted(gaps),
        "junctions": [list(p) for p, neighbors in sorted(graph.items()) if len(neighbors) >= 3],
        "components": _components(graph),
        **({"terminal_contacts": contacts or []} if shared_terminals else {}),
    }


def _components(graph):
    """Return stable physical connected components for diagnostics and receipts."""
    remaining = set(graph)
    result = []
    while remaining:
        seed = min(remaining)
        stack, component = [seed], set()
        while stack:
            point = stack.pop()
            if point in component:
                continue
            component.add(point)
            remaining.discard(point)
            stack.extend(graph[point] - component)
        result.append([list(point) for point in sorted(component)])
    return result
