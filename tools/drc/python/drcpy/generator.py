"""Calibre DRC command deck generation."""

from __future__ import annotations

from pathlib import Path

from caddefaults import Defaults
from cadcalibre.defaults import calibre_defaults
import re

from rcepy.config import DesignContext, RceConfig
from rcepy.textutil import q, write_text

from .rule_syntax import strip_comments
from .rule_select_config import selected_rule_names


_DRC_SELECT_CHECK = re.compile(
    r"^\s*(?:tvf::)?DRC\s+SELECT\s+CHECK(?:\s|$)", re.IGNORECASE | re.MULTILINE
)


def _is_compile_time_tvf(text: str) -> bool:
    return bool(re.search(r"^\s*#!\s*tvf(?:\s|$)", text[:4096], re.IGNORECASE))


def _contains_drc_select(text: str) -> bool:
    return bool(_DRC_SELECT_CHECK.search(strip_comments(text)))


def _existing_select_error() -> ValueError:
    return ValueError(
        "DRC Rule Select cannot be combined with an existing DRC SELECT "
        "CHECK or DRC SELECT CHECK BY LAYER statement"
    )


def _svrf_name(name: str) -> str:
    """Render a selected rule/check name as an unambiguous SVRF name."""
    return f'"{q(name)}"'


def _append_svrf(text: str, svrf: str, *, compile_time_tvf: bool) -> str:
    block = svrf.rstrip("\n")
    if compile_time_tvf:
        block = "tvf::VERBATIM {\n" + block + "\n}"
    return text.rstrip("\n") + "\n\n" + block + "\n"


def append_rule_select(
    text: str, cfg: RceConfig, *, compile_time_tvf: bool | None = None
) -> str:
    """Append the enabled DRC rule-group selection to the generated deck."""
    selections = selected_rule_names(cfg)
    if not selections:
        return text
    if _contains_drc_select(text):
        raise _existing_select_error()
    if compile_time_tvf is None:
        compile_time_tvf = _is_compile_time_tvf(text)
    statement = "DRC SELECT CHECK " + " ".join(map(_svrf_name, selections))
    return _append_svrf(text, statement, compile_time_tvf=compile_time_tvf)


def append_custom_svrf(
    text: str, cfg: RceConfig, *, compile_time_tvf: bool | None = None
) -> str:
    """Append enabled DRC custom SVRF verbatim to the generated deck."""
    if not cfg.flag("drc", "custom_svrf_enable", default=False):
        return text
    custom = cfg.text("drc", "custom_svrf_command")
    if not custom:
        return text
    if selected_rule_names(cfg) and _contains_drc_select(custom):
        raise _existing_select_error()
    if compile_time_tvf is None:
        compile_time_tvf = _is_compile_time_tvf(text)
    return _append_svrf(text, custom, compile_time_tvf=compile_time_tvf)


def generate_drc(cfg: RceConfig, ctx: DesignContext, *, defaults: Defaults | None = None) -> Path:
    runset = cfg.path("drc", "runset_file")
    if not runset:
        raise ValueError("Missing drc.runset_file in DRC TOML")
    runset_path = Path(runset)
    if not runset_path.is_file():
        raise FileNotFoundError(f"Cannot access DRC runset: {runset}")

    drc_dir = ctx.db_dir / "drc"
    rule_text = runset_path.read_text(errors="ignore")
    if selected_rule_names(cfg) and _contains_drc_select(rule_text):
        raise _existing_select_error()

    baseline = calibre_defaults("calibre-drc.svrf", defaults, ctx.log_dir)
    lines = [
        f'LAYOUT PRIMARY "{q(ctx.layout_cell)}"',
        f'LAYOUT PATH "{q(ctx.layout_path)}"',
        "LAYOUT SYSTEM GDSII",
        f'DRC RESULTS DATABASE "{q(str(drc_dir / "cal_drc.out"))}" ASCII',
        f'DRC SUMMARY REPORT "{q(str(ctx.log_dir / "cal_drc.sum"))}"',
        *baseline.get("default", []),
        f'INCLUDE "{q(str(runset_path))}"',
    ]
    text = append_rule_select("\n".join(lines) + "\n", cfg)
    text = append_custom_svrf(text, cfg)
    return write_text(ctx.log_dir / "drc.cal", text)
