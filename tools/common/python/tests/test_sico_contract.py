"""Installation identity, relocation and environment precedence are observable contracts."""

from pathlib import Path

import pytest

from sicoenv import RETIRED_NAMES, value
from sicopaths import IDENTITY_BYTES, MARKER, Installation, installation


def make_install(root):
    (root / MARKER).parent.mkdir(parents=True)
    (root / MARKER).write_bytes(IDENTITY_BYTES)
    (root / "bin").mkdir()
    (root / "tools/common").mkdir(parents=True)
    return root


def test_current_empty_value_is_authoritative_and_diagnostics_hide_secrets():
    env = {"SICO_API_KEY": "", "CAD_AGENT_API_KEY": "PRIVATE-KEY-VALUE"}
    assert value(env, "SICO_API_KEY", ("CAD_AGENT_API_KEY",), "default") == ""
    with pytest.raises(ValueError, match="was removed") as result:
        value({"CAD_AGENT_API_KEY": "PRIVATE-KEY-VALUE"}, "SICO_API_KEY", ("CAD_AGENT_API_KEY",))
    assert "PRIVATE-KEY-VALUE" not in str(result.value)


def test_legacy_only_and_absent_values():
    with pytest.raises(ValueError, match="was removed"):
        value({"CAD_PYTHON": "/python"}, "SICO_PYTHON", ("CAD_PYTHON",))
    assert value({}, "SICO_MODEL", ("CAD_AGENT_MODEL",), "default") == "default"


@pytest.mark.parametrize("current,retired", RETIRED_NAMES.items())
def test_retired_values_are_never_read(current, retired):
    class PresenceOnly(dict):
        def __getitem__(self, key):
            assert key not in retired, "retired value was inspected"
            return super().__getitem__(key)

    environment = PresenceOnly({name: "private" for name in retired})
    with pytest.raises(ValueError, match="was removed"):
        value(environment, current, retired)
    environment[current] = ""
    assert value(environment, current, retired) == ""


def test_installation_paths_survive_relocation_and_changed_cwd(tmp_path, monkeypatch):
    root = make_install(tmp_path / "old prefix")
    root.rename(tmp_path / "new prefix")
    root = tmp_path / "new prefix"
    monkeypatch.chdir(tmp_path)
    selected = installation({"SICO_HOME": str(root)}, anchor=root)
    assert selected.tools == root / "tools"
    assert selected.tool("sico") == root / "tools/sico"
    assert selected.icon("brand", "logo.png") == root / "share/sico/icons/brand/logo.png"
    assert selected.terminal_data == root / "share/qtermwidget5"


def test_site_symlink_resolves_to_the_same_installation(tmp_path):
    root = make_install(tmp_path / "version-1")
    alias = tmp_path / "current"
    alias.symlink_to(root, target_is_directory=True)
    assert installation({"SICO_HOME": str(alias)}, anchor=root).root == root


@pytest.mark.parametrize("configured", ["", "relative", "/unavailable/install"])
def test_invalid_current_root_never_falls_back_to_legacy(tmp_path, configured):
    root = make_install(tmp_path / "sico")
    with pytest.raises(ValueError):
        installation({"SICO_HOME": configured, "CAD_HOME": str(root)}, anchor=root)


def test_mixed_installations_are_rejected(tmp_path):
    first = make_install(tmp_path / "first")
    second = make_install(tmp_path / "second")
    with pytest.raises(ValueError, match="conflicts"):
        installation({"SICO_HOME": str(first)}, anchor=second)


def test_old_agent_roots_require_explicit_conversion(tmp_path):
    root = make_install(tmp_path / "sico")
    for name in ("SICO_ROOT", "CAD_AGENT_ROOT"):
        with pytest.raises(ValueError, match="Migrate"):
            installation({name: str(root / "tools/ai")}, anchor=root)
    with pytest.raises(ValueError, match="was removed"):
        installation({"CAD_HOME": str(root)})


def test_identity_and_path_escape_checks(tmp_path):
    root = make_install(tmp_path / "sico")
    selected = Installation(root)
    (root / "escape").symlink_to(tmp_path)
    for relative in ("../other", "/absolute", "escape/data", "a\\b", "bad\nname"):
        with pytest.raises(ValueError):
            selected.path(relative)
    with pytest.raises(ValueError):
        selected.tool("../ai")
    with pytest.raises(ValueError):
        selected.icon("brand", "../logo.png")
    (root / MARKER).write_text('{"product":"another product"}')
    with pytest.raises(ValueError, match="identity"):
        Installation(root)


def test_identity_cannot_be_borrowed_from_another_installation(tmp_path):
    root = make_install(tmp_path / "sico")
    marker = root / MARKER
    marker.unlink()
    external = tmp_path / "identity.json"
    external.write_bytes(IDENTITY_BYTES)
    marker.symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        Installation(root)


def test_source_tree_has_the_marked_product_layout():
    root = Path(__file__).resolve().parents[4]
    assert Installation(root).tools == root / "tools"
