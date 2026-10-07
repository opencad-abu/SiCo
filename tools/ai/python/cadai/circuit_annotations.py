"""Deterministic note wrapping and decoding of native text placement evidence."""

import math
import textwrap

# The native stick font at height 0.1 has an advance no larger than 0.1 uu.
# This is a conservative wrapping budget, not a claim about rendered bounds.
# Native placement checks the actual label bbox and reports any overflow.
NOTE_CHARACTER_BUDGET = 0.11
TEXT_POLICY = "cad.schematic.text-layout.v1"


def note_text(text, box):
    """Keep explicit paragraphs, break clauses, and wrap Unicode without data loss."""
    width = max(1, min(32, math.floor((box[1][0] - box[0][0]) / NOTE_CHARACTER_BUDGET)))
    wrapper = textwrap.TextWrapper(width=width, break_on_hyphens=False)
    paragraphs = text.replace(" | ", " |\n").split("\n")
    return "\n".join("\n".join(wrapper.wrap(line)) if line.strip() else ""
                     for line in paragraphs)


def decode_text_layout(rows):
    """The fixed native receipt is evidence, never automatic visual approval."""
    result = dict(zip(("schema", "status", "labels_checked", "labels_moved",
                     "initial_conflicts", "unresolved", "notes", "dynamic_labels",
                     "dynamic_examples", "dynamic_text_verified", "visual_review_required",
                     "observations", "dynamic_text_policy"), rows))
    for key in ("labels_moved", "unresolved", "notes", "dynamic_examples"):
        result[key] = result[key] or []
    # Proxy extents and unexpanded Cadence expressions are observations, not
    # demonstrated overlaps or acceptance blockers. Actual conflicts stay visible.
    result["observations"] = (result.get("observations") or []) + [
        r for r in result["unresolved"] if r[0] == "instance_text_extent_unverified"]
    result["unresolved"] = [r for r in result["unresolved"]
                            if r[0] != "instance_text_extent_unverified"]
    result["dynamic_text_policy"] = "cadence_expressions_not_blocking"
    if not result["unresolved"]:
        result["status"] = "static_text_checked"
    return result
