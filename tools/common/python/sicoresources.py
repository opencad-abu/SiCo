"""Product resources from one marked installation, independent of the caller's CWD."""

from pathlib import Path

from sicoenv import value
from sicopaths import installation


def current(environment=None):
    return installation(environment, anchor=Path(__file__).resolve().parents[3])


def icon(category, name, environment=None):
    path = current(environment).icon(category, name)
    return path if path.is_file() else None


def font(name, environment=None):
    """One bundled product font; callers never search the caller's CWD."""
    path = current(environment).font(name)
    return path if path.is_file() else None


def manual_path(environment=None):
    """The reviewed offline HTML manual entry, when this installation ships it."""
    try:
        path = current(environment).path("docs/html/index.html")
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def terminal_data(environment=None):
    """The explicit data override never affects product branding."""
    import os

    environment = os.environ if environment is None else environment
    installed = current(environment)
    configured = value(environment, "SICO_AI_QTERMWIDGET_DATA_PATH",
                       ("CAD_AI_QTERMWIDGET_DATA_PATH",))
    root = installed.terminal_data
    if configured is not None:
        root = Path(configured)
        if not configured.strip() or not root.is_absolute() or not root.is_dir():
            raise ValueError("SICO_AI_QTERMWIDGET_DATA_PATH must name an absolute directory")
        root = root.resolve()
    return root


def terminal_resource(relative, environment=None):
    root = terminal_data(environment)
    requested = Path(relative)
    if requested.is_absolute() or ".." in requested.parts or "\\" in relative:
        raise ValueError("Expected a relative terminal resource path")
    path = (root / requested).resolve()
    if root not in path.parents:
        raise ValueError("Terminal resource escapes its data directory")
    return path if path.is_file() else None
