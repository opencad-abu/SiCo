"""The property-access operators are present in every SKILL prompt surface."""

from pathlib import Path

from sico.codex.backend_instructions import INSTRUCTIONS as CODEX_INSTRUCTIONS

from cadai.launch import assistant_instructions
from cadai.mcp import SERVER_INSTRUCTIONS, McpServer
from cadai.skill_access_guidance import INSTRUCTIONS


class _Client:
    def call(self, method, arguments):
        return True, {}

    def close(self):
        pass


def test_access_guidance_covers_read_write_and_discovery_operators():
    for syntax in ("obj~>prop", "obj~>prop=value", "obj~>?", "obj~>??",
                   "obj->?", "obj->??", "cv~>instances~>name"):
        assert syntax in INSTRUCTIONS


def test_all_skill_prompt_surfaces_share_access_guidance():
    assert INSTRUCTIONS in SERVER_INSTRUCTIONS
    assert INSTRUCTIONS in assistant_instructions()
    assert INSTRUCTIONS in CODEX_INSTRUCTIONS


def test_mcp_initialize_publishes_access_guidance():
    response = McpServer(_Client()).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    instructions = response["result"]["instructions"]
    assert "obj~>?" in instructions and "obj~>??" in instructions


def test_skill_document_teaches_object_exploration():
    root = Path(__file__).resolve().parents[2]
    skill = (root / "skills/cadence-skill/SKILL.md").read_text(encoding="utf-8")
    assert "obj~>?" in skill and "obj~>??" in skill
    assert "skdfref/topic/Retrieving_the_List_of_Attribute_Names" in skill
