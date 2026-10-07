"""Bounded, conflict-directed combinations of free junction coordinate moves."""

from .circuit_geometry_schema import bounds
from .template_route_detour import _guides
from .template_route_tracks import _free_tracks

MAX_MOVES = 2
MAX_GENERATED = 512
MAX_FRONTIER = 64


def motion_states(edges, groups, scope, fixed, boxes, grid, clearance, feedback, *, max_units,
                  allowed=lambda moved: True):
    """Use the previous failed branch to choose the next movable endpoints.

    Yield coordinate maps only, never geometry. The caller owns all graph and
    obstacle checks. A state combines at most two consistent unit assignments;
    units may share points on different axes, never disagree on the same axis.
    """
    ends = [{tuple(ps[0]), tuple(ps[-1])} for _, ps in groups]
    movable = set().union(*(ends[i] for i in scope)) - fixed
    movable -= set().union(*(ends[i] for i in range(len(groups)) if i not in scope))
    units = [(axis, points & movable) for axis, points in _free_tracks(edges, fixed)
             if len(points & movable) > 1 and points & set().union(*ends) <= movable]
    units += [(axis, {point}) for point in sorted(movable) for axis in (0, 1)]
    units.sort(key=lambda row: (-len(row[1]), row[0], sorted(row[1])))
    choices = {}

    def candidates(index):
        if index in choices:
            return choices[index]
        axis, points = units[index]
        start, end = bounds([list(p) for p in points])
        values = _guides(boxes, start, end, grid, clearance)["xy"[axis]]
        choices[index] = [{(p, axis): value for p in points}
                          for value in values if value != start[axis]]
        return choices[index]

    pending, seen, generated = [({}, 0)], {()}, 0
    while pending:
        assigned, depth = pending.pop()
        moved = {p: tuple(assigned.get((p, k), p[k]) for k in (0, 1))
                 for p, _ in assigned}
        feedback["depth"] = depth
        feedback["generated"] = generated
        yield moved
        if depth >= MAX_MOVES:
            continue
        if generated >= MAX_GENERATED:
            feedback["truncated"] = True
            continue
        blocked = feedback.get("blocked")
        focus = set(blocked) if blocked is not None else movable
        relevant = [i for i, (_, points) in enumerate(units) if points & focus][:max_units]
        children = []
        # Round robin axes/units before trying a further obstacle guide.
        for rank in range(max((len(candidates(i)) for i in relevant), default=0)):
            for i in relevant:
                if rank >= len(choices[i]):
                    continue
                change = choices[i][rank]
                if any(key in assigned and assigned[key] != value
                       for key, value in change.items()):
                    continue
                trial = dict(assigned)
                trial.update(change)
                key = tuple(sorted(trial.items()))
                if key in seen:
                    continue
                if generated >= MAX_GENERATED:
                    feedback["truncated"] = True
                    break
                seen.add(key)
                generated += 1
                moved = {p: tuple(trial.get((p, k), p[k]) for k in (0, 1))
                         for p, _ in trial}
                if not allowed(moved):
                    continue
                children.append((trial, depth + 1))
            if generated >= MAX_GENERATED:
                feedback["truncated"] = True
                break
        # Explore a failed state's targeted extension before unrelated siblings.
        pending.extend(reversed(children))
        if len(pending) > MAX_FRONTIER:
            feedback["truncated"] = True
        pending = pending[-MAX_FRONTIER:] if MAX_FRONTIER > 0 else []
