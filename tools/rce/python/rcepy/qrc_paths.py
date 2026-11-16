"""Protect FinFET Quantus paths through its CCL-to-SKILL conversion."""

import re

from .command_paths import command_path
from .config_context import DesignContext


_PATH_OPTION = re.compile(
    r'(-(?!temporary_directory_name\b)[a-z_]*(?:file|directory)(?:_name)?)\s+"([^"\n]+)"'
)


def qrc_path_text(ctx: DesignContext, text: str) -> str:
    return _PATH_OPTION.sub(
        lambda match: f'{match[1]} "{command_path(ctx, match[2], "qrc")}"', text,
    )
