"""Explicit, single-reference compatibility properties for composed owners.

Use only for named legacy attributes while supported scripts migrate to the
owner API. Never copy state or infer forwarding by scanning an object.
"""

from operator import attrgetter


def compat_attribute(path: str, *, writable: bool = False):
    getter = attrgetter(path)
    if not writable:
        return property(getter)
    owner, _, name = path.rpartition(".")
    resolve_owner = attrgetter(owner)

    def set_value(instance, value):
        setattr(resolve_owner(instance), name, value)

    return property(getter, set_value)
