"""Operational instructions for the native Codex task adapter.

INSTRUCTIONS is the single authority consumed by CodexBackend.  Do not
add new prose here — extend the relevant sico/prompts/ fragment instead
and pass it via assemble().
"""

from cadai.circuit_guidance import INSTRUCTIONS as CIRCUIT_INSTRUCTIONS
from cadai.pdk_preparation import INSTRUCTIONS as PDK_INSTRUCTIONS
from ..knowledge.tools import INSTRUCTIONS as KNOWLEDGE_INSTRUCTIONS
from ..prompt_registry.registry import assemble as _assemble

# Assembled once at import time; all fragment text lives in sico/prompts/.
INSTRUCTIONS = _assemble(
    "codex_backend",
    pdk_instructions=PDK_INSTRUCTIONS,
    circuit_instructions=CIRCUIT_INSTRUCTIONS,
    knowledge_instructions=KNOWLEDGE_INSTRUCTIONS,
)
