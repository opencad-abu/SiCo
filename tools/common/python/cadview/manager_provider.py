"""Adapt provider call signatures without retrying provider failures."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Callable, Mapping


CatalogLoader = Callable[..., object]


def invoke_provider(
    loader: CatalogLoader,
    path: Path,
    values: Mapping[str, object],
) -> object:
    """Call one provider with only the keyword arguments it accepts."""

    try:
        signature = inspect.signature(loader)
    except (TypeError, ValueError):
        return loader(path, **dict(values))

    parameters = signature.parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    keyword_parameters = {
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    kwargs = {
        name: value
        for name, value in values.items()
        if accepts_kwargs or name in keyword_parameters
    }
    return loader(path, **kwargs)


__all__ = ["CatalogLoader", "invoke_provider"]
