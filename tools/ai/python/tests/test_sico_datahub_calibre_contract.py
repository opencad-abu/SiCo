"""Calibre DataHub records, receipts and registry surfaces."""

import hashlib
import os

import pytest
from sico_datahub import contracts
from sico_datahub.calibre.catalog import RuleCatalog
from sico_datahub.calibre.jobs import JobStore
from sico_datahub.calibre.outcomes import DrcResult, DrcViolation, LvsResult
from sico_datahub.calibre.provenance import CalibreProvenance, RunsetIdentity
from sico_datahub.calibre.receipts import JobReceipt
from sico_datahub.codec import artifact_ref
from sico_datahub.errors import HubError
from sico_datahub.registry import default_registry


def _artifact(path, text="rule\n"):
    path.write_text(text, encoding="utf-8")
    return artifact_ref(path)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_calibre_registry_exposes_catalog_and_paged_results():
    registry = default_registry()
    assert contracts.ACTIONS["sico.calibre.drc.v1"] == (
        "catalog", "check", "probe", "result", "status", "submit"
    )
    assert contracts.ACTIONS["sico.calibre.lvs.v1"] == (
        "check", "export", "probe", "result", "status", "submit"
    )
    drc = registry.get("sico.calibre.drc.v1", 1)
    assert set(drc.actions["catalog"].request.fields) == {
        "runset_path", "offset", "limit"
    }
    assert set(drc.actions["result"].request.fields) == {
        "run_ref", "offset", "limit"
    }
    assert drc.actions["catalog"].response_schema == "calibre.drc.catalog.v1"
    assert set(drc.actions["check"].request.fields) == {
        "run_ref", "config_path", "work_root", "candidate_path", "bbox"
    }
    assert drc.actions["check"].response_schema == "calibre.drc.check.v1"
    lvs = registry.get("sico.calibre.lvs.v1", 1)
    assert set(lvs.actions["result"].request.fields) == {
        "run_ref", "offset", "limit"
    }
    assert set(lvs.actions["check"].request.fields) == {
        "run_ref", "config_path", "work_root",
        "candidate_path", "source_path", "bbox",
    }
    assert lvs.actions["check"].response_schema == "calibre.lvs.check.v1"
    assert set(lvs.actions["export"].request.fields) == {
        "export_ref", "config_path", "work_root"
    }
    assert lvs.actions["export"].response_schema == "calibre.lvs.export.v1"


def test_calibre_provenance_requires_digest_and_bound_artifacts(tmp_path):
    runset = _artifact(tmp_path / "drc.cal")
    provenance = CalibreProvenance(
        runset=runset,
        dependencies_sha256=_digest("rule\n"),
        tool_version="Calibre fixture",
        run_mode="Flat",
        top_cell="inv",
        run_ref="drc-1",
        options={"rule_select_checks": ["M1.W.1"]},
    )
    assert provenance.to_dict()["runset"]["path"] == str(runset.path)
    assert provenance.to_dict()["run_ref"] == "drc-1"
    with pytest.raises(HubError, match="dependency digest"):
        CalibreProvenance(runset, "not-a-digest")
    with pytest.raises(HubError, match="runset"):
        CalibreProvenance(str(runset.path), _digest("rule\n"))
    with pytest.raises(HubError, match="dependency count"):
        RunsetIdentity(runset, _digest("rule\n"), 0)
    with pytest.raises(HubError, match="rule layer"):
        RuleCatalog(runset, _digest("rule\n"), layers=tuple("L" for _ in range(201)))


def test_drc_signoff_is_only_clean_full_scope(tmp_path):
    runset = _artifact(tmp_path / "drc.cal")
    provenance = CalibreProvenance(runset, _digest("rule\n"))
    full = DrcResult(
        "clean", 0, provenance=provenance, scope="full"
    )
    fragment = DrcResult(
        "clean", 0, provenance=provenance, scope="fragment"
    )
    violation = DrcResult(
        "violations",
        1,
        checks={"M1.W.1": 1},
        violations=(DrcViolation("M1.W.1", "BBOX 0 0 1 1"),),
        provenance=provenance,
    )
    assert full.signoff_eligible is True
    assert fragment.signoff_eligible is False
    assert violation.signoff_eligible is False
    assert violation.to_dict()["violations_truncated"] is False
    assert LvsResult("correct").to_dict()["findings_truncated"] is False


def test_drc_result_rejects_unbounded_violation_list():
    violations = tuple(
        DrcViolation("c" + str(index)) for index in range(201)
    )
    with pytest.raises(HubError, match="Too many DRC violations"):
        DrcResult("violations", 201, violations=violations)


def test_job_receipt_roundtrip_and_kind_state_validation(tmp_path):
    config = _artifact(tmp_path / "flow.toml", "[run]\n")
    runset = _artifact(tmp_path / "drc.cal")
    receipt = JobReceipt(
        run_ref="drc-roundtrip",
        kind="drc",
        state="violations",
        config=config,
        work_root=str(tmp_path),
        created_at="2026-10-23T00:00:00+00:00",
        runset=runset,
        dependencies_sha256=_digest("rule\n"),
    )
    assert JobReceipt.from_dict(receipt.to_dict()) == receipt
    legacy = receipt.to_dict()
    for name in (
        "candidate", "candidate_source", "effective_deck",
        "effective_config", "lvs_log", "scope",
    ):
        legacy.pop(name)
    assert JobReceipt.from_dict(legacy) == receipt
    with pytest.raises(HubError, match="job state"):
        JobReceipt(
            run_ref="drc-state",
            kind="drc",
            state="correct",
            config=config,
            work_root=str(tmp_path),
            created_at="2026-10-23T00:00:00+00:00",
        )
    with pytest.raises(HubError, match="run_ref"):
        JobReceipt(
            run_ref="../escape",
            kind="drc",
            state="queued",
            config=config,
            work_root=str(tmp_path),
            created_at="2026-10-23T00:00:00+00:00",
        )


def test_job_store_is_private_durable_and_bounded(tmp_path):
    store = JobStore(tmp_path)
    assert store.root.stat().st_mode & 0o777 == 0o700
    config = _artifact(tmp_path / "flow.toml", "[run]\n")
    receipt = JobReceipt(
        run_ref="drc-store",
        kind="drc",
        state="queued",
        config=config,
        work_root=str(tmp_path),
        created_at="2026-10-23T00:00:00+00:00",
    )
    store.create(receipt)
    assert store.load("drc-store") == receipt
    with pytest.raises(HubError, match="already exists"):
        store.create(receipt)
    store.write_result("drc-store", {"schema": "fixture", "ok": True})
    assert store.read_result("drc-store") == {"schema": "fixture", "ok": True}
    manifest = store.write_metadata(
        "drc-store", "svdb-manifest", {"schema": "fixture", "entries": []}
    )
    assert manifest.size > 0
    with pytest.raises(HubError, match="run_ref"):
        store.load("../escape")
    for path in (tmp_path / ".sico_datahub", store.root):
        assert path.stat().st_mode & 0o777 == 0o700
    assert os.stat(store.job_path("drc-store")).st_mode & 0o777 == 0o600


def test_catalog_serializes_only_bounded_metadata(tmp_path):
    runset = _artifact(tmp_path / "drc.cal")
    catalog = RuleCatalog(
        runset,
        _digest("rule\n"),
        groups=("G1",),
        counts={"G1": 1},
        members={"G1": ("M1.W.1",)},
        layers=("M1",),
        source="static",
    )
    value = catalog.to_dict()
    value["members"]["G1"].append("mutated")
    assert catalog.members["G1"] == ("M1.W.1",)
