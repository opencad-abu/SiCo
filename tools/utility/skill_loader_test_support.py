"""Inspect generated loader contracts in source integration regressions."""

from pathlib import Path
from functools import lru_cache

from skill_loader_contract import contract, dependencies, manifest, members

ROOT = Path(__file__).resolve().parents[2]


class LoaderContract:
    def __init__(self, path):
        groups = manifest(ROOT)
        relative = str(path.resolve().relative_to(ROOT))
        name = next(key for key, group in groups.items() if group["output"] == relative)
        self.rows = [row for key in dependencies(groups, name)
                     for row in contract(ROOT, groups[key])[1]]
        self.exports = {export for row in self.rows for export in row[2]}
        self.revisions = dict(revision for row in self.rows for revision in row[3])
        self.paths = [row[0] for row in self.rows]

    def requires(self, name):
        return name in self.exports

    def revision(self, name):
        return self.revisions[name]

    def index(self, suffix):
        suffix = suffix.strip('"')
        return next(index for index, path in enumerate(self.paths) if path.endswith(suffix))

    def contains_source(self, suffix):
        return any(path.endswith(suffix) for path in self.paths)


@lru_cache(maxsize=None)
def loader_contract(relative):
    return LoaderContract(ROOT / "tools" / relative)


def ordered_sources(name):
    groups = manifest(ROOT)
    return [ROOT / path for key in dependencies(groups, name)
            for path in members(ROOT, groups[key])]
