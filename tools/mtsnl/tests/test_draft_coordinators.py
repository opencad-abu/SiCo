"""Receipt ownership independent of Qt widgets and worker processes."""

from dataclasses import replace
from types import SimpleNamespace

from mtsnetlistor.config import canonical_request_digest
from mtsnetlistor.gui.cell_drafts import CellDraftStore
from mtsnetlistor.gui.controller import ControllerState
from mtsnetlistor.gui.draft_requests import build_request
from mtsnetlistor.gui.generation_coordinator import GenerationCoordinator


def test_store_incarnation_survives_clear_and_publication_does_not_edit_parameters():
    store = CellDraftStore()
    key = ("source", "top", "schematic")
    first = store.add(key)
    revision = store.revision(key, "spectre")
    changed = replace(first, publication=replace(first.publication, target_cell="renamed"))
    store.save(changed)
    assert store.view(key, "spectre").simulator is first.simulator
    assert store.view(key, "hspiceD").publication is changed.publication
    assert store.revision(key, "spectre") == revision
    store.clear()
    store.add(key)
    assert not store.matches(key, "spectre", revision)


def test_old_token_cannot_claim_new_run_with_identical_digest(tmp_path):
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="ascii")
    store = CellDraftStore()
    request = build_request(cds, (store.add(("source", "top", "schematic")),), multiple=False)
    result = SimpleNamespace(request_digest=canonical_request_digest(request))
    owner = GenerationCoordinator()
    owner.submitted(10, request)
    owner.begin()
    owner.submitted(20, request)
    assert owner.receive(ControllerState(token=10, generation=result)) is False
    assert owner.result is None
    current = SimpleNamespace(request_digest=result.request_digest)
    assert owner.receive(ControllerState(token=20, generation=current)) is True
    assert owner.result is current


def test_catalog_advance_retains_generation_and_frozen_publication(tmp_path):
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="ascii")
    store = CellDraftStore()
    request = build_request(cds, (store.add(("source", "top", "schematic")),), multiple=False)
    result = SimpleNamespace(request_digest=canonical_request_digest(request))
    owner = GenerationCoordinator()
    owner.submitted(10, request, publication=request)
    assert owner.receive(ControllerState(token=11, generation=result)) is True
    assert owner.take_publication() is request
    assert owner.take_publication() is None


def test_missing_or_mismatched_generation_digest_is_rejected(tmp_path):
    cds = tmp_path / "cds.lib"
    cds.write_text("", encoding="ascii")
    store = CellDraftStore()
    request = build_request(cds, (store.add(("source", "top", "schematic")),), multiple=False)
    for result in (SimpleNamespace(), SimpleNamespace(request_digest="wrong")):
        owner = GenerationCoordinator()
        owner.submitted(10, request, publication=request)
        assert owner.receive(ControllerState(token=10, generation=result)) is False
        assert owner.result is None
        assert owner.take_publication() is None
