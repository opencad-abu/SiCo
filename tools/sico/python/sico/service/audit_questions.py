"""Engineering question schema, bounded values and presentation-neutral identity."""

from __future__ import annotations

from ..core.contracts import identifier

NEW_WINDOW_OPTION = "新开窗口…"

QUESTION = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "minLength": 1, "maxLength": 96},
        "header": {"type": "string", "minLength": 1, "maxLength": 80},
        "question": {"type": "string", "minLength": 1, "maxLength": 2000},
        "options": {
            "type": "array", "minItems": 0, "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": 200},
                    "description": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                "required": ["label", "description"], "additionalProperties": False,
            },
        },
    },
    "required": ["id", "header", "question", "options"], "additionalProperties": False,
}

def bounded(value, limit, *, empty=False):
    if (
        not isinstance(value, str) or len(value) > limit
        or (not empty and not value.strip())
        or any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in value)
    ):
        raise ValueError("请输入有效且长度合适的答复")
    return value

def questions(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        raise ValueError("Audit requires one to three questions")
    result = []
    for question in value:
        if not isinstance(question, dict) or question.get("isSecret", False) is not False:
            raise ValueError("Secret input is not supported by the audit journal")
        options = question.get("options") or []
        if not isinstance(options, list) or len(options) > 6:
            raise ValueError("Invalid audit options")
        choices = [
            {"label": bounded(o["label"], 200), "description": bounded(o["description"], 1000)}
            for o in options
        ]
        if len({o["label"] for o in choices}) != len(choices):
            raise ValueError("Duplicate audit options")
        result.append({
            "id": identifier(question["id"]),
            "header": bounded(question["header"], 80),
            "question": bounded(question["question"], 2000),
            "options": choices,
        })
    if len({q["id"] for q in result}) != len(result):
        raise ValueError("Duplicate audit question IDs")
    return result

def same_host_questions(expected, incoming):
    """Match presentation-only option changes without broadening a decision."""
    def canonical(rows):
        result = {}
        for question in rows:
            options = set()
            for option in question["options"]:
                label = option["label"].removesuffix(" (Recommended)")
                if label in options:
                    return None
                # Descriptions and recommendation markers are presentation
                # details.  The decision identity is the stable question id,
                # wording and candidate labels; changing a path or rationale
                # must not create another PDK prompt for the same task.
                options.add(label)
            result[question["id"]] = (question["question"], options)
        return result
    return canonical(expected) is not None and canonical(expected) == canonical(incoming)
