"""Ask for a missing API credential in the new GUI; keep it in process memory only."""

from __future__ import annotations

import os

from sico import PRODUCT_NAME

from .si_prompt import ask_text


class CredentialCancelled(Exception):
    """The user dismissed the credential prompt without starting a model task."""


def credential_environment(key_name):
    """The service resolves and validates the credential variable before showing Qt."""
    env = dict(os.environ)
    if not env.get(key_name):
        key, accepted = ask_text(
            None, PRODUCT_NAME + " API Key",
            f"请输入 {key_name}（仅保存在本次会话内存中）：", password=True)
        if not accepted or not key.strip():
            raise CredentialCancelled()
        env[key_name] = key.strip()
    return env
