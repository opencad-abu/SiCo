"""Draft ownership and explicit conflict resolution for project resource defaults."""

from copy import deepcopy

from sico.agent_resources.schema import upgrade_manifest, validate_manifest


class SettingsDraft:
    def __init__(self):
        self.manifest = None
        self.saved = None
        self.document = None
        self.latest = None
        self.blocked = False

    @property
    def dirty(self):
        return self.manifest is not None and self.manifest != self.saved

    def load(self, document):
        if self.dirty or self.blocked:
            self.latest = deepcopy(document)
            return False
        self.accept(document)
        return True

    def accept(self, document):
        self.document = deepcopy(document)
        self.latest, self.blocked = None, False
        self.manifest = upgrade_manifest(document["manifest"])
        self.saved = deepcopy(self.manifest)

    def rebase(self):
        if self.latest is None:
            raise ValueError("请先重新读取工程默认")
        document, draft = self.latest, self.manifest
        self.accept(document)
        self.manifest = draft

    def replace(self, kind, index, row):
        value = deepcopy(self.manifest)
        if index < 0:
            value[kind].append(row)
        else:
            value[kind][index] = row
        validate_manifest(value)
        self.manifest = value

    def toggle(self, kind, index):
        row = deepcopy(self.manifest[kind][index])
        row["enabled"] = not row["enabled"]
        self.replace(kind, index, row)

    def remove(self, kind, index):
        self.manifest[kind].pop(index)
