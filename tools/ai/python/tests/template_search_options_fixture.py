"""Qualified candidates with distinct immutable identities for search option tests."""

import copy

from template_adapt_fixture import reusable, target_case

from cadai.template_build import summary
from cadai.template_reuse_build import content_ref
from cadai.template_target import target_topology


def candidates(count=4):
    reference = reusable()
    rows = []
    for granularity in ("atomic", "block", "subsystem", "ip")[:count]:
        row = copy.deepcopy(reference)
        row["granularity"] = granularity
        row["template_ref"] = content_ref(row)
        row["summary"] = summary(row)
        rows.append(row)
    return sorted(rows, key=lambda row: row["template_ref"])


def core_input(reference, **kwargs):
    args = target_case(reference, **kwargs)
    return target_topology(args["spec"], args["bindings"]), args["mapping"]


def prepare_request(reference, **kwargs):
    args = target_case(reference, **kwargs)
    return {
        key: args[key] for key in ("spec", "bindings", "mapping", "allowed_rule_refs")
    } | {"options": {"match_mode": "core", "allowed_tiers": ["private"],
                     "return_alternatives": 0}}
