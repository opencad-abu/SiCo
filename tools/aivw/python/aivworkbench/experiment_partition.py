"""Evaluator-owned case reservation with a public commitment, never vectors.

Whole stimulus trajectories are indivisible. Values are generated once and
replayed from the private file. Hiding is a controller access-policy duty:
filesystem permissions alone cannot isolate an Agent running as the same user.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
from typing import Any, Mapping, Sequence

from .acceptance import finite
from .workspace import sha256_file


def case_digest(case: Mapping[str, Any]) -> str:
    """Ignore labels; hash the entire physical stimulus and measurement setup."""
    if not isinstance(case, Mapping) or set(case) != {"kind", "stimulus", "measurement"} or not case["stimulus"] or not case["measurement"]:
        raise ValueError("partition unit must be a whole experiment case")
    encoded = json.dumps(case, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _sha(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("expected a SHA-256 identity")
    return value


def stratified_points(bounds: Mapping[str, Sequence[float]], count: int, *, seed: int) -> list[dict[str, float]]:
    """Latin hypercube samples: one sample per stratum in every dimension."""
    if type(count) is not int or not 2 <= count <= 256 or type(seed) is not int:
        raise ValueError("invalid sampling count or seed")
    if not bounds or len(bounds) > 16:
        raise ValueError("sampling dimensions outside budget")
    rng = random.Random(seed)
    rows: list[dict[str, float]] = [{} for _ in range(count)]
    for name in sorted(bounds):
        interval = bounds[name]
        if not isinstance(name, str) or not name or len(interval) != 2:
            raise ValueError("invalid sampling axis")
        low, high = (finite(v, name) for v in interval)
        if not low < high or not (high - low) < float("inf"):
            raise ValueError("sampling axis must increase finitely")
        values = [low + (high-low) * (i + rng.random()) / count for i in range(count)]
        rng.shuffle(values)
        for row, value in zip(rows, values):
            row[name] = value
    return rows


def reserve_holdout(private_root: Path, *, public_cases: Sequence[Mapping[str, Any]],
                    hidden_cases: Sequence[Mapping[str, Any]], seed: int,
                    domain_digest: str, policy_digest: str) -> dict[str, Any]:
    """Persist a new private reservation; return only salted commitment/count.

    Call only within the evaluator. Neither input vectors nor seed belong in
    Agent context, prompts, public plans, or detailed gate feedback.
    """
    public = {case_digest(case) for case in public_cases}
    hidden = [case_digest(case) for case in hidden_cases]
    if not hidden or len(hidden) != len(set(hidden)) or public.intersection(hidden):
        raise ValueError("holdout is empty, duplicated or overlaps public experiments")
    if type(seed) is not int:
        raise ValueError("holdout identity is incomplete")
    _sha(domain_digest); _sha(policy_digest)
    root = Path(private_root)
    if not root.is_absolute() or root.is_symlink() or not root.parent.is_dir():
        raise ValueError("holdout root must be a new absolute evaluator directory")
    if any(p.is_symlink() for p in root.parents):
        raise ValueError("holdout path traverses a symlink")
    root.mkdir(mode=0o700)
    record = {"schema_version": 1, "kind": "reserved_whole_case_holdout",
              "domain_digest": domain_digest, "policy_digest": policy_digest,
              "seed": seed, "nonce": secrets.token_hex(32), "cases": list(hidden_cases),
              "public_case_digests": sorted(public), "state": "reserved_before_fit"}
    path = root / "reservation.json"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(record, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return {"schema_version": 1, "locked": True, "public_values_included": False,
            "case_count": len(hidden), "commitment_sha256": sha256_file(path),
            "domain_digest": domain_digest, "policy_digest": policy_digest,
            "state": "reserved_before_fit"}


def freeze_candidate(private_root: Path, commitment: Mapping[str, Any], *,
                     candidate_digest: str, contract_digest: str) -> dict[str, str]:
    """Bind one immutable candidate before evaluator use; forbid re-selection."""
    root = Path(private_root)
    path = root / "reservation.json"
    if any(p.is_symlink() for p in (root, path, *root.parents)) or sha256_file(path) != commitment["commitment_sha256"]:
        raise ValueError("holdout commitment mismatch")
    _sha(candidate_digest); _sha(contract_digest)
    result = {"candidate_digest": candidate_digest, "contract_digest": contract_digest,
              "holdout_commitment": commitment["commitment_sha256"]}
    fd = os.open(str(root / "candidate-freeze.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True)
        stream.write("\n")
    return result
