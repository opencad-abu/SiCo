"""Deterministic same-directory mapping shards, independent of logical identity."""

from pathlib import PurePosixPath

from .codec import LIMIT, TARGET, atomic, encode, fail, read, relative

FIELDS = {
    "index": "packages",
    "package": "dependencies pdks rules files",
    "category": "items",
    "template": "items",
    "file": "items",
    "sources": "items",
    "collection": "gaps",
    "topology": "masters devices nets ports connections",
    "placement": "instances anchors annotations",
    "routing": "nodes segments crossings",
    "relation": "items",
    "parameter": "items",
    "reuse": "optional_groups boundaries constraints",
    "qualification": "",
    "symbol": "terminals anchors outline labels",
    "layout": "bindings",
}


def fields(role):
    return FIELDS[role].split() + ["evidence"]


def mapping(root, value, owner, observe, depth=0, seen=None):
    if not isinstance(value, dict):
        fail("mapping required: " + owner)
    if "$parts" not in value:
        for key, row in value.items():
            if key.startswith("$") or len(encode({key: row})) > TARGET:
                fail("invalid/oversize mapping row: " + key)
        return value
    if set(value) != {"$parts"} or depth >= 4 or not value["$parts"]:
        fail("invalid shard depth/descriptor")
    if not isinstance(value["$parts"], list):
        fail("shard descriptors must be a list")
    seen = set() if seen is None else seen
    result, previous = {}, None
    for part in value["$parts"]:
        if not isinstance(part, dict) or set(part) != {"path", "sha256", "count", "first", "last"}:
            fail("invalid shard fields")
        name = part["path"]
        parent, child = PurePosixPath(owner), PurePosixPath(name)
        if child.parent != parent.parent or not child.name.startswith(parent.stem + "."):
            fail("shard must use a same-directory suffix")
        path = relative(root, name)
        if name in seen or type(part["count"]) is not int or part["count"] < 1:
            fail("repeated shard or invalid count")
        seen.add(name)
        data, digest = read(path, part["sha256"])
        observe(name, digest)
        rows = mapping(root, data, name, observe, depth + 1, seen)
        keys = list(rows)
        if (
            not keys
            or keys != sorted(keys)
            or len(keys) != part["count"]
            or keys[0] != part["first"]
            or keys[-1] != part["last"]
            or (previous is not None and previous >= keys[0])
        ):
            fail("shard range/count/order mismatch")
        result.update(rows)
        previous = keys[-1]
        if len(result) > 100000:
            fail("mapping exceeds 100000 rows")
    return result


class Writer:
    def __init__(self, root):
        self.root, self.files = root, {}

    def raw(self, name, value, inventory):
        digest = atomic(relative(self.root, name), value)
        if inventory:
            self.files[name] = digest
        return digest

    def put(self, name, value, role, inventory=True):
        out = dict(value)
        candidates = [key for key in fields(role) if key in out]
        for key in candidates:
            if not isinstance(out[key], dict):
                fail("mapping required: " + key)
            for ident, row in out[key].items():
                if len(encode({ident: row})) > TARGET:
                    fail("indivisible row exceeds 8 KiB: " + ident)
        # Several medium maps may together exceed the file limit. Shard largest first.
        for key in sorted(candidates, key=lambda k: (-len(encode(out[k])), k)):
            if len(encode(out[key])) > TARGET or len(encode(out)) > LIMIT:
                out[key] = self.shard(name, key, out[key], inventory)
        return self.raw(name, out, inventory)

    def shard(self, owner, field, values, inventory):
        chunks, chunk = [], {}
        for key, value in sorted(values.items()):
            if chunk and len(encode({**chunk, key: value})) > TARGET:
                chunks.append(chunk)
                chunk = {}
            chunk[key] = value
        if chunk:
            chunks.append(chunk)
        if not chunks:
            return {}

        def tree(name, groups, depth):
            if depth > 4:
                fail("shard depth exceeds four")
            # Build a tree top-down so every child's basename extends its parent's.
            if len(groups) <= 12:
                children = groups
            else:
                size = 12 ** max(1, (len(groups).bit_length() // 3 - 1))
                children = [groups[i : i + size] for i in range(0, len(groups), size)]
            parts = []
            for i, group in enumerate(children):
                child = str(PurePosixPath(name).with_suffix("")) + f".{i:04d}.json"
                if isinstance(group, list):
                    body, keys, count = tree(child, group, depth + 1)
                else:
                    body, keys, count = group, sorted(group), len(group)
                digest = self.raw(child, body, inventory)
                parts.append(
                    dict(path=child, sha256=digest, count=count, first=keys[0], last=keys[-1])
                )
            return (
                {"$parts": parts},
                [parts[0]["first"], parts[-1]["last"]],
                sum(p["count"] for p in parts),
            )

        name = str(PurePosixPath(owner).with_suffix("")) + "." + field + ".json"
        body, _, _ = tree(name, chunks, 1)
        # The virtual field container is inline, not an extra file/depth.
        return body
