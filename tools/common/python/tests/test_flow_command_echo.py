"""Flow startup reports log locations without echoing full commands."""

from pathlib import Path


CAD_ROOT = Path(__file__).resolve().parents[3]


def test_export_and_lvs_startup_do_not_echo_full_commands():
    for relative in ("lvs/skill++/STAGECB.ils", "lvs/skill++/LVSRUN.ils"):
        source = (CAD_ROOT / relative).read_text(encoding="utf-8")
        assert "::CMD=" not in source, relative
        assert "::LOG=" in source, relative
