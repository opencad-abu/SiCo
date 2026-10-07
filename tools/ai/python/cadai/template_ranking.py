"""Deterministic quality_then_origin.v1 ordering of hard-validated candidates."""

from .template_schema import TemplateError


def ranking_key(candidate):
    if candidate["eligibility"] != "eligible" or candidate["rank_features"] is None:
        raise TemplateError("ranking requires an eligible candidate")
    rank = candidate["rank_features"]
    return (
        rank["adaptation_cost"],
        -rank["target_coverage"],
        rank["style_penalty"],
        rank["origin_priority"],
        candidate["template_ref"],
        rank["mapping_digest"],
    )
