"""Read immutable old-to-new artifact locations from an activated migration."""

from pathlib import Path

from sicomigration import admission_payload


def locations(state_root):
    """Return the reviewed source-path mapping, or None for an unmigrated root."""
    state_root = Path(state_root)
    # The legacy root is a valid pre-activation workspace.  It cannot pass
    # activation admission because its sibling is itself, so keep the
    # unmigrated read path explicit here.
    if state_root.name == ".cad":
        return None
    payload = admission_payload(state_root)
    if payload is None:
        return None
    marker, inventory = payload
    if inventory.get("source_identity") != marker["source_identity"]:
        raise ValueError("Migration location inventory has another source identity")
    result = {}
    for row in inventory.get("locations", ()):
        if (not isinstance(row, dict) or set(row) != {"source", "target", "sha256"}
                or not isinstance(row["source"], str) or not isinstance(row["target"], str)
                or row["source"] in result):
            raise ValueError("Invalid migration location entry")
        result[row["source"]] = row["target"]
    return result


def target_for(state_root, source):
    mapping = locations(state_root)
    if mapping is None:
        return None
    source = str(Path(source))
    target = mapping.get(source)
    if target is None:
        raise ValueError("Historical artifact has no reviewed migration location")
    return Path(target)
