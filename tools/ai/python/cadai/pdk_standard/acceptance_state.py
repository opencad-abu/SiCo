"""Small workflow predicate: is metadata collection complete enough to accept?"""

from __future__ import annotations


def collection_complete(package) -> bool:
    """Return whether readiness has no remaining collection findings.

    Acceptance is a separate real-device stage. This predicate only decides
    whether the workflow should hand off to it; it never records acceptance or
    grants design readiness.
    """
    from .readiness import issues

    # An update to an already published package has invalidated its publication.
    # That is a generation conflict, not a first-time acceptance handoff.
    if (package.overlay is not None
            and package.base.manifest.get("publication") is not None):
        return False
    # This is a collection-stage predicate only. `issues` is the static
    # readiness projection; live source/resource checks happen in acceptance.
    return next(issues(package), None) is None


__all__ = ["collection_complete"]
