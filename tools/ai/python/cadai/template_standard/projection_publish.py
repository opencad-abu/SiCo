"""Keep shared publication destinations free of private installs and stale payloads."""

from .codec import fail, relative
from .package import index, load


def check_destination(destination, catalog):
    for location in catalog.locations:
        source = location.path.resolve()
        if source == destination or source in destination.parents or destination in source.parents:
            fail("public projection output must be separate from source catalogs")
    check_contents(destination)


def check_contents(destination):
    """Also called under the publisher lock, closing the concurrent-index race."""
    if not destination.exists():
        return
    installed, observed = index(destination)
    allowed = {destination / name for name in observed}
    allowed.add(destination / ".standard.lock")
    for row in installed["packages"].values():
        if row["roots"]:
            fail("public projection output contains private resource roots")
        root = relative(destination, row["path"])
        package = load(root)
        if any(e["detail_level"] == "raw" for e in package["tables"]["template"]["items"].values()):
            fail("public projection output contains a raw package")
        allowed.update(root / name for name in package["observed"])
    for path in destination.rglob("*"):
        if path.is_symlink() or path.is_file() and path not in allowed:
            fail("public projection output contains untracked payloads")
