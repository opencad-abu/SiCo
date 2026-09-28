"""Shared Qt application and discovery fixtures for selector tests."""

from __future__ import annotations
import os
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from drcpy.rule_select_qt import QApplication
from drcpy.rule_groups import RuleGroupInfo

_APPLICATION = None


@pytest.fixture(scope="module")
def application() -> QApplication:
    global _APPLICATION
    _APPLICATION = QApplication.instance() or QApplication([])
    yield _APPLICATION


@pytest.fixture
def groups() -> RuleGroupInfo:
    return RuleGroupInfo(
        groups=("METAL", "VIA", "SHARED", "OPAQUE"),
        counts={"METAL": 2, "VIA": 2, "SHARED": 2, "OPAQUE": 7},
        members={
            "METAL": ("M1.WIDTH", "COMMON.CHECK"),
            "VIA": ("V1.SPACE", "V2.SPACE"),
            "SHARED": ("COMMON.CHECK", "SHARED.ONLY"),
            "OPAQUE": (),
        },
        source="calibre",
    )
