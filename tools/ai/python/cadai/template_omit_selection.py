"""Bounded composition of the immutable single-group omission rule."""

from .template_rule_omit import omit_group
from .template_schema import TemplateError

MAX_OMITTED_GROUPS = 8


def omit_selected(record, groups):
    """Each selected whole group passes the existing deletion authority."""
    if (not isinstance(groups, (list, tuple)) or len(groups) > MAX_OMITTED_GROUPS
            or any(not isinstance(group, str) for group in groups)
            or len(set(groups)) != len(groups)):
        raise TemplateError("needs_adaptation: select at most 8 unique optional group IDs")
    topology, removed = omit_group(record, [])
    for group in groups:
        topology, change = omit_group({**record, "topology": topology}, [group])
        for field in removed:
            removed[field] = sorted(set(removed[field]) | set(change[field]))
    return topology, removed
