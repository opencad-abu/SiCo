from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import subprocess

import pytest

from cadprofile.model import ProfileError, load_profile
from cadprofile.skill_data import (
    publish_profile,
    publish_profile_source,
    render_form_data,
    render_profile_toml,
    write_form_data,
)


CAD_ROOT = Path(__file__).resolve().parents[3]
ENTRY = CAD_ROOT / "common/python/sico-profile"


def _metadata(flow: str, version: int = 1) -> str:
    return (
        "[cad_config]\n"
        'format = "sico-flow-profile"\n'
        f"version = {version}\n"
        f'flow = "{flow}"\n\n'
    )


def _drc_profile(extra: str = "") -> str:
    return _metadata("DRC") + (
        "[run]\n"
        'root = "${DRC_DB_DIR}"\n'
        'run_type = "Current Host"\n'
        'queue_name = "normal"\n'
        'server_name = "localhost"\n\n'
        "[input]\n"
        'type = "OA"\n\n'
        "[input.layout]\n"
        'lib = "demo"\n'
        'cell = "top"\n'
        'view = "layout"\n\n'
        "[drc]\n"
        'tool = "Calibre"\n'
        'runset_name = "SMIC28"\n'
        'runset_file = "${PDK_ROOT}/Calibre/DRC/rules.drc"\n'
        'run_mode = "Hier"\n'
        "rule_select_enable = true\n"
        'rule_select_groups = ["METAL"]\n'
        'rule_select_checks = ["M1.W.1"]\n'
        "custom_svrf_enable = false\n"
        'custom_svrf_command = ""\n\n'
        "[runtime]\n"
        'cpus = "4"\n'
        + extra
    )


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


__all__ = [name for name in globals() if not name.startswith('__')]
