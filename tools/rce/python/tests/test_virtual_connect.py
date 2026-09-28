from __future__ import annotations

import sys
from pathlib import Path

import pytest


PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from rcepy.config import RceConfig  # noqa: E402
from rcepy.gen_lvs import generate_lvs  # noqa: E402
from rcepy.generators import generate_all  # noqa: E402
from xrc_test_support import make_xrc_config  # noqa: E402


def _generic_lvs_config(tmp_path: Path) -> RceConfig:
    return RceConfig(
        raw={
            "run": {"run_dir": str(tmp_path / "run")},
            "input": {
                "type": "CDL+GDS",
                "cdl": {"file": "top.cdl", "cell": "top"},
                "gds": {"file": "top.gds", "cell": "top"},
            },
            "lvs": {"tool": "Calibre"},
            "extract": {"tool": "QRC"},
            "runtime": {"lvs_cpus": "1", "ext_cpus": "1"},
        },
        config_path=tmp_path / "lvs.toml",
    )


def _render(
    tmp_path: Path,
    engine: str,
    virtual_connect: str | None,
    names: str = "",
) -> str:
    if engine == "lvs":
        cfg = _generic_lvs_config(tmp_path)
        output = cfg.context().log_dir / "lvs.cal"
    else:
        cfg, paths = make_xrc_config(tmp_path)
        output = paths["runset"]

    if virtual_connect is not None:
        cfg = cfg.replace('lvs', value={**cfg.section('lvs'), **{
                "virtual_connect": virtual_connect,
                "virtual_connect_names": names,
            }})
    if engine == "lvs":
        generate_lvs(cfg, cfg.context())
    else:
        generate_all(cfg, cfg.context())
    return output.read_text(encoding="utf-8")


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
def test_virtual_connect_name_all_uses_quoted_calibre_wildcard(
    tmp_path: Path, engine: str
) -> None:
    text = _render(tmp_path, engine, "(nil t)", "?")

    assert 'VIRTUAL CONNECT NAME "?"' in text
    assert "VIRTUAL CONNECT COLON" not in text


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
def test_virtual_connect_quotes_custom_names_and_combines_colon_mode(
    tmp_path: Path, engine: str
) -> None:
    text = _render(tmp_path, engine, "(t t)", '"VDD" VSS?')

    assert "VIRTUAL CONNECT COLON YES" in text
    assert 'VIRTUAL CONNECT COLON "YES"' not in text
    assert 'VIRTUAL CONNECT NAME "VDD" "VSS?"' in text


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
def test_virtual_connect_name_enabled_requires_a_pattern(
    tmp_path: Path, engine: str
) -> None:
    with pytest.raises(
        ValueError,
        match="Virtual Connect by Name is enabled.*no net-name pattern",
    ):
        _render(tmp_path, engine, "(nil t)")


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
def test_missing_virtual_connect_configuration_remains_disabled(
    tmp_path: Path, engine: str
) -> None:
    text = _render(tmp_path, engine, None)

    assert "VIRTUAL CONNECT" not in text


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
@pytest.mark.parametrize("colon,name", [(False, False), (True, False), (False, True), (True, True)])
def test_explicit_virtual_connect_switches_control_generated_commands(
    tmp_path: Path, engine: str, colon: bool, name: bool
) -> None:
    cfg = _generic_lvs_config(tmp_path) if engine == "lvs" else make_xrc_config(tmp_path)[0]
    cfg = cfg.replace("lvs", value={
        **cfg.section("lvs"), "virtual_connect_enable": colon,
        "virtual_connect_name_enable": name, "virtual_connect_names": "?",
        "virtual_connect": "(t t)",  # Explicit switches override stale legacy data.
    })
    if engine == "lvs":
        path = generate_lvs(cfg, cfg.context())
    else:
        path = generate_all(cfg, cfg.context())["extract"]
    text = path.read_text()
    assert ("VIRTUAL CONNECT COLON YES" in text) == colon
    assert ('VIRTUAL CONNECT NAME "?"' in text) == name


@pytest.mark.parametrize("engine", ("lvs", "xrc"))
def test_legacy_virtual_connect_whitespace_keeps_both_commands(tmp_path: Path, engine: str) -> None:
    text = _render(tmp_path, engine, " ( t  \n t ) ", "?")
    assert "VIRTUAL CONNECT COLON YES" in text
    assert 'VIRTUAL CONNECT NAME "?"' in text


def test_colon_switch_alone_does_not_require_name_pattern(tmp_path: Path) -> None:
    cfg = _generic_lvs_config(tmp_path)
    cfg = cfg.replace('lvs', 'virtual_connect_enable', value=True)
    text = generate_lvs(cfg, cfg.context()).read_text()
    assert "VIRTUAL CONNECT COLON YES" in text
    assert "VIRTUAL CONNECT NAME" not in text


def test_invalid_legacy_virtual_connect_is_not_silently_ignored(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="virtual_connect"):
        _render(tmp_path, "lvs", "(unknown t)", "?")
