"""Instance bridge identity and project-local discovery address."""

import hashlib
import json
import socket

from ..storage.roots import agent_root

BRIDGE_PROTOCOL = "cad_ai_instance_bridge.v1"


def discovery_path(launch_dir, context, *, node=None):
    # Include the host because launch directories can be shared over NFS.
    key = json.dumps([socket.gethostname() if node is None else node,
                      context.instance_id, context.generation])
    return agent_root(launch_dir) / "bridges" / (
        hashlib.sha256(key.encode()).hexdigest() + ".json")


def locate_path(launch_dir, context):
    """Resolve one recorded identity across shared nodes; ambiguity is an error."""
    import os
    from ..storage.journal import open_private
    from .framing import strict_json
    from .bridge_tunnel import node_name

    matches = []
    for path in (agent_root(launch_dir) / 'bridges').glob('*.json'):
        try:
            with os.fdopen(open_private(path, os.O_RDONLY), 'rb') as stream:
                row = strict_json(stream.read(8193))
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if (row.get('instance_id'), row.get('generation')) != (
                context.instance_id, context.generation):
            continue
        node = node_name(row.get('node', socket.gethostname()))
        if path != discovery_path(launch_dir, context, node=node):
            raise ValueError('Bridge discovery identity mismatch')
        matches.append(path)
    if len(matches) > 1:
        raise ValueError('Bridge identity exists on multiple execution nodes')
    if not matches:
        raise FileNotFoundError('Bridge identity has no descriptor')
    return matches[0]
