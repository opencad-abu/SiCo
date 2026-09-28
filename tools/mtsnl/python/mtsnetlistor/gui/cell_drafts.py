"""Single owner of editable cell publication and simulator drafts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple

from ..model import CornerExport


class CellIdentity(NamedTuple):
    library: str
    cell: str
    view: str


@dataclass(frozen=True)
class PublicationSelection:
    target_library: str = ""
    target_cell: str = ""
    publish_symbol: bool = False
    overwrite_symbol: bool = False
    publish_text: bool = False
    overwrite_text: bool = False


@dataclass(frozen=True)
class SimulatorDraft:
    models: tuple = ()
    temp: str = ""
    tnom: float | None = None
    scale: str = ""
    scalem: float | None = None
    reltol: float | None = None
    gmin: str = ""
    options: tuple = ()
    corner_export: CornerExport = CornerExport()
    temperature_mode: str = "fixed"


@dataclass(frozen=True)
class CellDraft:
    """Transient projection referencing the two independently owned values."""

    identity: CellIdentity
    dialect: str
    simulator: SimulatorDraft
    publication: PublicationSelection

    @property
    def key(self):
        return self.identity


@dataclass(frozen=True)
class DraftRevision:
    incarnation: int
    cell_revision: int
    dialect_revision: int


class CellDraftStore:
    def __init__(self):
        self._publications = {}
        self._simulators = {}
        self._revisions = {}
        self._dialect_revisions = {}
        self._incarnations = {}
        self._dirty = set()
        self._serial = 0

    def __contains__(self, key):
        return key in self._publications

    def __bool__(self):
        return bool(self._publications)

    def keys(self):
        return tuple(self._publications)

    def add(self, key, dialect="spectre", simulator=None, publication=None):
        key = CellIdentity(*key)
        if key in self:
            raise ValueError("Source cell is already selected")
        self._serial += 1
        self._incarnations[key] = self._serial
        self._revisions[key] = 0
        self._publications[key] = publication or PublicationSelection()
        self._simulators[(key, dialect)] = simulator or SimulatorDraft()
        return self.view(key, dialect)

    def remove(self, key):
        self._publications.pop(key, None)
        self._revisions.pop(key, None)
        self._incarnations.pop(key, None)
        for pair in tuple(self._simulators):
            if pair[0] == key:
                self._simulators.pop(pair)
        for pair in tuple(self._dialect_revisions):
            if pair[0] == key:
                self._dialect_revisions.pop(pair)
        self._dirty = {pair for pair in self._dirty if pair[0] != key}

    def clear(self):
        for key in self.keys():
            self.remove(key)

    def view(self, key, dialect):
        key = CellIdentity(*key)
        publication = self._publications[key]
        simulator = self._simulators.get((key, dialect), SimulatorDraft())
        return CellDraft(key, dialect, simulator, publication)

    def save(self, draft, *, edited=False):
        if draft.key not in self:
            raise ValueError("Source cell is no longer selected")
        self._publications[draft.key] = draft.publication
        self._simulators[(draft.key, draft.dialect)] = draft.simulator
        if edited:
            self.edited(draft.key, draft.dialect)

    def edited(self, key, dialect):
        if key not in self:
            return
        self._dirty.add((key, dialect))
        self._revisions[key] += 1
        pair = (key, dialect)
        self._dialect_revisions[pair] = self._dialect_revisions.get(pair, 0) + 1

    def dirty(self, key, dialect=None):
        return ((key, dialect) in self._dirty if dialect is not None
                else any(pair[0] == key for pair in self._dirty))

    def revision(self, key, dialect):
        return DraftRevision(self._incarnations[key], self._revisions[key],
                             self._dialect_revisions.get((key, dialect), 0))

    def matches(self, key, dialect, revision, *, automatic=True):
        if key not in self:
            return False
        current = self.revision(key, dialect)
        return (current.incarnation == revision.incarnation
                and (current.dialect_revision == revision.dialect_revision if automatic
                     else current.cell_revision == revision.cell_revision))

    def apply_defaults(self, key, dialect, report, *, automatic=True):
        if key not in self or self.dirty(key, dialect if automatic else None):
            return False
        draft = self.view(key, dialect).simulator
        models = tuple((row.enabled, str(row.file), row.section, row.label)
                       for row in report.models)
        self._simulators[(key, dialect)] = replace(
            draft, models=models or draft.models, temp=report.temp_text or draft.temp,
            scale=report.scale_text or draft.scale, gmin=report.gmin_text or draft.gmin,
        )
        self._dirty.discard((key, dialect))
        return True
