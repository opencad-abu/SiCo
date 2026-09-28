"""Stable identity of an inspected design target: its contents, not its request.

The same unchanged cellview is the same engineering decision however many times
it is inspected; changed contents are a new decision that needs a new answer.
The inspection's ``target_ref`` identifies one retained observation, so it
cannot key a decision that outlives the call that produced it.
"""

from __future__ import annotations

import hashlib
import json

CONTENT_FIELDS = ("target", "content_state", "counts", "contents", "writable")
KEY_FIELD = "target_decision_key"


def content_digest(observation):
    """Digest the content fields that identify an inspected target observation."""
    if not isinstance(observation, dict):
        return ""
    stable = {field: observation[field] for field in CONTENT_FIELDS if field in observation}
    if not stable:
        return ""
    try:
        encoded = json.dumps(stable, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return ""
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def decision_key(observation):
    """Key one existing-design decision by its question and inspected contents.

    A replay that already settled the decision carries the key it was derived
    from, so repeating an inspection keeps addressing the same decision.
    """
    if not isinstance(observation, dict):
        return ""
    carried = observation.get(KEY_FIELD)
    if isinstance(carried, str) and carried:
        return carried
    question = observation.get("selection_question")
    question_id = question.get("id") if isinstance(question, dict) else None
    digest = content_digest(observation)
    if not isinstance(question_id, str) or not question_id.strip() or not digest:
        return ""
    return question_id + ":" + digest
