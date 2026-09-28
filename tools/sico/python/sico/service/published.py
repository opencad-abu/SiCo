"""Owned, deeply immutable JSON views built only by background publishers."""


def _immutable(*_args, **_kwargs):
    raise TypeError("Published data is read-only")


class FrozenDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


class FrozenList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = _immutable
    reverse = sort = __iadd__ = __imul__ = _immutable

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def freeze(value):
    if isinstance(value, (FrozenDict, FrozenList)):
        return value
    if isinstance(value, dict):
        return FrozenDict((key, freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return FrozenList(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    """Explicit mutable copy for a backend operation or a consumer-owned edit."""
    if isinstance(value, dict):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value
