"""Explicit widget dependencies for conversation rendering and commands."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationWidgets:
    parent: object
    notices: object
    session_notices: object
    input: object
    display: object
    target: object
    token_usage: object
    status: object
    status_bar: object
    send: object
    stop: object
    resume: object
    abandon: object
    latest: object
    resume_action: object
    return_action: object
    host_notice_status: object
    tabs: object
    conversation: object
    task_panel: object
