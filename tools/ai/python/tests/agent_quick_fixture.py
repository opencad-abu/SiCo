"""Backend broker injection belongs to test setup, never to the UI interface."""

from sico_ui.quick_input import QuickInput as FrontendQuickInput


def QuickInput(window, broker, notice):
    window.sessions.broker = broker
    return FrontendQuickInput(window, notice)
