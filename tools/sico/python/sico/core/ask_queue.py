"""Ask queue limits and explicit per-instance submission order."""

PENDING_LIMIT = 16
SERVICE_CAPACITY = 64
MAX_SEQUENCE = 2147483647


class QueueFull(ValueError):
    """The request was refused before entering the session queue."""


def ask_sequence(value):
    if type(value) is not int or not 1 <= value <= MAX_SEQUENCE:
        raise ValueError("Invalid Ask sequence")
    return value


def pending_order(record):
    # Legacy requests retain arrival order after explicitly sequenced requests.
    return (record["message"].get("ask_sequence", float("inf")), record["accepted_at"])


def queue_head(inbox):
    row = inbox.active or next(iter(inbox.pending), None)
    if row is None:
        return None
    return dict(input_id=row["id"], ask_sequence=row["message"].get("ask_sequence"))
