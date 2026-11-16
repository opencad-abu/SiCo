import json
from pathlib import Path

import pytest

from aivworkbench.experiment_partition import stratified_points, reserve_holdout, freeze_candidate


def case(v):
    return {"kind":"dc","stimulus":{"supply":v,"load":1000.},"measurement":{"window":[1.,2.],"unit":"V"}}


def test_latin_hypercube_is_repeatable_and_covers_every_stratum():
    points=stratified_points({"voltage":[0.,1.],"load":[100.,200.]},12,seed=99)
    assert points==stratified_points({"load":[100.,200.],"voltage":[0.,1.]},12,seed=99)
    assert sorted(int(p["voltage"]*12) for p in points)==list(range(12))
    assert sorted(int((p["load"]-100.)/100.*12) for p in points)==list(range(12))


def test_reservation_hides_values_and_rejects_overwrite(tmp_path):
    root=tmp_path/"private"
    commitment=reserve_holdout(root,public_cases=[case(1.)],hidden_cases=[case(1.234567)],seed=56789,domain_digest="d"*64,policy_digest="e"*64)
    text=json.dumps(commitment)
    assert "1.234567" not in text and "56789" not in text and "cases" not in commitment
    assert (root.stat().st_mode & 0o777)==0o700
    assert ((root/"reservation.json").stat().st_mode & 0o777)==0o600
    with pytest.raises(FileExistsError):reserve_holdout(root,public_cases=[case(1.)],hidden_cases=[case(2.)],seed=1,domain_digest="d"*64,policy_digest="e"*64)
    freeze_candidate(root,commitment,candidate_digest="c"*64,contract_digest="f"*64)
    with pytest.raises(FileExistsError):freeze_candidate(root,commitment,candidate_digest="a"*64,contract_digest="f"*64)


def test_partition_rejects_overlap_and_duplicate_whole_cases(tmp_path):
    with pytest.raises(ValueError):reserve_holdout(tmp_path/"p",public_cases=[case(1.)],hidden_cases=[case(1.)],seed=1,domain_digest="d"*64,policy_digest="e"*64)
    with pytest.raises(ValueError):reserve_holdout(tmp_path/"p",public_cases=[],hidden_cases=[case(1.),case(1.)],seed=1,domain_digest="d"*64,policy_digest="e"*64)


def test_freeze_rejects_tampered_reservation(tmp_path):
    root=tmp_path/"p"
    commitment=reserve_holdout(root,public_cases=[],hidden_cases=[case(1.)],seed=1,domain_digest="d"*64,policy_digest="e"*64)
    with (root/"reservation.json").open("a") as f:f.write(" ")
    with pytest.raises(ValueError):freeze_candidate(root,commitment,candidate_digest="c"*64,contract_digest="f"*64)
