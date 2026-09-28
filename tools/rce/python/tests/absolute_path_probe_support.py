from __future__ import annotations

import os
from pathlib import Path

from skill_probe_support import run_virtuoso_source

try:
    import tomllib as tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.9 compatibility
    import tomli as tomllib  # noqa: F401 -- compatibility export for source probes


CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _install_tree(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    install.mkdir()
    (install / "tools").symlink_to(CAD_ROOT, target_is_directory=True)
    return install


def _write(path: Path, text: str = "probe\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_probe(
    tmp_path: Path,
    *,
    flow: str,
    entry: str,
    commands: tuple[str, ...],
    env_updates: dict[str, str],
) -> str:
    launch = tmp_path / "launch"
    launch.mkdir(exist_ok=True)
    replay = launch / f"{flow.lower()}_absolute_paths.il"
    log = launch / f"{flow.lower()}_absolute_paths.log"
    replay.write_text(
        "\n".join(
            (
                f'load("{entry}")',
                *commands,
                f'printf("{flow}_ABSOLUTE_PATHS_OK\\n")',
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(
        replay.read_text(encoding="utf-8"), launch,
        env_updates=env_updates, log_path=log,
    )
    assert f"{flow}_ABSOLUTE_PATHS_OK" in output
    return output
