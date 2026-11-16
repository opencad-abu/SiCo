"""Template outline -> formal design binding -> pin-grounded TB -> native Maestro."""

from test_template_smic28_gui import verify_archived_inv_to_smic28


def test_drawn_symbol_to_smic_tb_and_maestro(tmp_path, monkeypatch):
    verify_archived_inv_to_smic28(tmp_path, monkeypatch, outline=True)
