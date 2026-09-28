"""Shared runtime support for standalone CAD PyQt5 applications."""

from typing import TYPE_CHECKING

from .branding import logo_text
from .environment import (
    QT_ENV_VARIABLES,
    check_xcb_runtime,
    prepare_qt_environment,
)
from .lifecycle import (
    ParentIdentity,
    capture_parent_identity,
    finalize_gui_exit,
    install_parent_monitor,
)
from .protocol import (
    PROTOCOL_VERSION,
    TransferDocument,
    TransferRecord,
    read_transfer,
    write_transfer,
)
from .transfer import atomic_publish_text

if TYPE_CHECKING:
    # Runtime access is deliberately lazy so ``import cadgui`` remains usable
    # for CLI tools and worker processes that do not have PyQt5 installed.
    from .library_browser import (
        CategoryTreeWidget,
        LibraryBrowserWidget,
        LibraryTreeWidget,
    )
    from .library_browser_controller import CatalogController
    from .library_manager import LibraryManagerWidget

__version__ = "20260814.cadgui.v1"

__all__ = [
    "PROTOCOL_VERSION",
    "ParentIdentity",
    "QT_ENV_VARIABLES",
    "CatalogController",
    "CategoryTreeWidget",
    "LibraryBrowserWidget",
    "LibraryTreeWidget",
    "LibraryManagerWidget",
    "TransferDocument",
    "TransferRecord",
    "atomic_publish_text",
    "capture_parent_identity",
    "check_xcb_runtime",
    "finalize_gui_exit",
    "install_parent_monitor",
    "logo_text",
    "prepare_qt_environment",
    "read_transfer",
    "write_transfer",
]


def __getattr__(name: str):
    """Lazily expose widgets without importing PyQt5 for non-GUI callers."""

    if name == "LibraryBrowserWidget":
        from .library_browser import LibraryBrowserWidget

        return LibraryBrowserWidget
    if name == "CategoryTreeWidget":
        from .library_browser import CategoryTreeWidget

        return CategoryTreeWidget
    if name == "LibraryTreeWidget":
        from .library_browser import LibraryTreeWidget

        return LibraryTreeWidget
    if name == "CatalogController":
        from .library_browser_controller import CatalogController

        return CatalogController
    if name == "LibraryManagerWidget":
        from .library_manager import LibraryManagerWidget

        return LibraryManagerWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
