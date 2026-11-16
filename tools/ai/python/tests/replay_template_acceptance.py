"""Replay the frozen synthetic acceptance split with full/indexed matcher comparison."""

import argparse
import json
from pathlib import Path

from replay_template_baseline import source_identity
from template_acceptance_manifest import acceptance_report, manifest_for
from template_search_corpus import DATASET_DIGEST, cases
from test_template_index_recall import _case_pool, _load_index, _safe_exact_indexed_refs

from cadai.template_schema import digest
from cadai.template_search import search_exact


def replay(output):
    corpus = cases()
    if digest(corpus) != DATASET_DIGEST:
        raise ValueError("frozen corpus changed")
    manifest = manifest_for(corpus)
    observations = []
    output.mkdir(parents=True, exist_ok=True)
    for case in corpus:
        records = _case_pool(case)
        index = _load_index(output, records, digest(case))
        retained = _safe_exact_indexed_refs(index, case["target"], records)
        no_loss = True
        labeled = None
        for record in records:
            full = search_exact(case["target"], [record],
                                {"max_query_time_ms": 10000, "max_match_states": 200000},
                                port_maps={record["template_ref"]:
                                           case["port_maps"].get(record["template_ref"], {})})
            if full["status"] in {"invalid_request", "unavailable"}:
                raise ValueError("acceptance runner failed: " + json.dumps(full["issues"]))
            if record["template_ref"] == case["candidates"][0]["template_ref"]:
                labeled = full
            if full["status"] != "no_match" and record["template_ref"] not in retained:
                no_loss = False
        observations.append(dict(
            id=case["id"], input_digest=digest(case), status=labeled["status"],
            mapping_matches=case["expected_mapping"] is None or
                labeled["best_match"]["mapping"] == case["expected_mapping"],
            no_new_recall_loss=no_loss,
        ))
    report = acceptance_report(manifest, observations)
    report["source"] = source_identity()
    for name, value in (("manifest", manifest), ("observations", observations), ("report", report)):
        with (output / (name + ".json")).open("x") as stream:
            json.dump(value, stream, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    result = replay(parser.parse_args().output)
    print(json.dumps({k: v for k, v in result.items() if k not in {"source", "cases"}}))
    raise SystemExit(0 if result["machine_passed"] and not result["failed"] else 1)
