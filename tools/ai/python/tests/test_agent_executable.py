from __future__ import annotations

import os
from pathlib import Path

import pytest

from cadai.launch import prepare_agent_runtime_environment, resolve_agent_executable
from assistant_installation_fixture import assistant_installation


@pytest.fixture(autouse=True)
def isolated_installation(tmp_path, monkeypatch):
    assistant_installation(tmp_path / "installation", monkeypatch)


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "codex-cli 0.162.1\\n"\n', encoding="ascii")
    path.chmod(0o755)
    return path


def _node_launcher(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env node\n", encoding="ascii")
    path.chmod(0o755)
    return path


def test_explicit_codex_override_precedes_environment_bundle_and_site_wrapper(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "installation/tools/ai"
    _executable(install_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    _executable(tmp_path / "installation" / "bin" / "codex")
    explicit = _executable(tmp_path / "explicit" / "codex")
    configured = _executable(tmp_path / "configured" / "codex")

    resolved = resolve_agent_executable(
        "codex",
        str(explicit),
        {
            "SICO_HOME": str(tmp_path / "installation"),
            "SICO_CODEX_CLI": str(configured),
        },
        install_root=install_root,
    )

    assert resolved == str(explicit.resolve())


def test_codex_environment_override_precedes_bundle_and_site_wrapper(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "installation/tools/ai"
    _executable(install_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex")
    _executable(tmp_path / "installation" / "bin" / "codex")
    configured = _executable(tmp_path / "configured" / "codex")

    resolved = resolve_agent_executable(
        "codex",
        environment={
            "SICO_HOME": str(tmp_path / "installation"),
            "SICO_CODEX_CLI": str(configured),
        },
        install_root=install_root,
    )

    assert resolved == str(configured.resolve())


@pytest.mark.parametrize("with_explicit", (True, False))
def test_claude_keeps_site_wrapper_priority(
    tmp_path: Path, with_explicit: bool
) -> None:
    site = _executable(tmp_path / "installation" / "bin" / "claude")
    explicit = _executable(tmp_path / "explicit" / "claude")
    configured = _executable(tmp_path / "configured" / "claude")

    resolved = resolve_agent_executable(
        "claude",
        str(explicit) if with_explicit else None,
        {
            "SICO_HOME": str(tmp_path / "installation"),
            "SICO_CLAUDE_CLI": str(configured),
        },
    )

    assert resolved == str(site.resolve())


@pytest.mark.parametrize("with_explicit", (True, False))
def test_claude_keeps_configured_fallback_when_site_wrapper_is_missing(
    tmp_path: Path, with_explicit: bool
) -> None:
    explicit = _executable(tmp_path / "explicit" / "claude")
    configured = _executable(tmp_path / "configured" / "claude")

    resolved = resolve_agent_executable(
        "claude",
        str(explicit) if with_explicit else None,
        {
            "SICO_HOME": str(tmp_path / "installation"),
            "SICO_CLAUDE_CLI": str(configured),
        },
    )

    assert resolved == str((explicit if with_explicit else configured).resolve())


def test_bundled_codex_precedes_site_wrapper_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_root = tmp_path / "installation/tools/ai"
    bundled = _executable(
        install_root
        / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    )
    _executable(tmp_path / "installation" / "bin" / "codex")
    path_codex = _executable(tmp_path / "path" / "codex")
    monkeypatch.setenv("PATH", str(path_codex.parent))

    resolved = resolve_agent_executable(
        "codex",
        environment={"SICO_HOME": str(tmp_path / "installation"), "PATH": str(path_codex.parent)},
        install_root=install_root,
    )

    assert resolved == str(bundled.resolve())


def test_site_wrapper_remains_a_codex_fallback_when_bundle_is_missing(
    tmp_path: Path,
) -> None:
    wrapper = _executable(tmp_path / "installation" / "bin" / "codex")

    resolved = resolve_agent_executable(
        "codex",
        environment={"SICO_HOME": str(tmp_path / "installation"), "PATH": ""},
        install_root=tmp_path / "installation/tools/ai",
    )

    assert resolved == str(wrapper.resolve())


def test_codex_bundle_is_preferred_without_node_or_path_codex(tmp_path: Path) -> None:
    install_root = tmp_path / "installation/tools/ai"
    bundled = _executable(
        install_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    )
    resolved = resolve_agent_executable(
        "codex",
        environment={"PATH": "/usr/bin:/bin"},
        install_root=install_root,
    )
    assert resolved == str(bundled.resolve())


def test_codex_bundle_rejects_a_separate_release_root(tmp_path: Path) -> None:
    source_root = tmp_path / "source" / "ai"
    release_root = tmp_path / "installation/tools/ai"
    _executable(
        release_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    )

    with pytest.raises(ValueError, match="conflicts"):
        resolve_agent_executable(
            "codex",
            environment={"PATH": "/usr/bin:/bin"},
            install_roots=(release_root, source_root),
        )


def test_codex_bundle_rejects_conflicting_configured_release_root(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source" / "ai"
    release_root = tmp_path / "installation/tools/ai"
    _executable(
        release_root / "runtime/codex/x86_64-unknown-linux-musl/bin/codex"
    )

    with pytest.raises(ValueError, match="conflicts"):
        resolve_agent_executable(
            "codex",
            environment={
                "SICO_AI_RELEASE_ROOT": str(release_root),
                "PATH": "/usr/bin:/bin",
            },
            install_root=source_root,
        )


def test_agent_resolution_rejects_unknown_agent() -> None:
    with pytest.raises(ValueError, match="unsupported AI agent"):
        resolve_agent_executable("unknown", environment={})


@pytest.mark.parametrize("home_names_bin", (True, False))
def test_codex_node_launcher_repairs_path_from_either_nodejs_home_shape(
    tmp_path: Path, home_names_bin: bool
) -> None:
    launcher = _node_launcher(tmp_path / "codex")
    prefix = tmp_path / "node-prefix"
    node = _executable(prefix / "bin" / "node")
    node_home = node.parent if home_names_bin else prefix
    original_path = str(tmp_path / "path-without-node")
    environment = {"PATH": original_path, "NODEJS_HOME": str(node_home)}

    prepare_agent_runtime_environment("codex", str(launcher), environment)

    assert environment["PATH"].split(os.pathsep) == [
        str(node.parent),
        original_path,
    ]


def test_codex_node_launcher_keeps_an_existing_node_path(tmp_path: Path) -> None:
    launcher = _node_launcher(tmp_path / "codex")
    binary_directory = tmp_path / "bin"
    _executable(binary_directory / "node")
    environment = {
        "PATH": str(binary_directory),
        "NODEJS_HOME": str(tmp_path / "incorrect-node-home"),
    }

    prepare_agent_runtime_environment("codex", str(launcher), environment)

    assert environment["PATH"] == str(binary_directory)


def test_codex_node_launcher_fails_before_opening_terminal_without_node(
    tmp_path: Path,
) -> None:
    launcher = _node_launcher(tmp_path / "codex")

    with pytest.raises(RuntimeError, match="Codex launcher requires Node.js"):
        prepare_agent_runtime_environment(
            "codex",
            str(launcher),
            {
                "PATH": str(tmp_path / "path-without-node"),
                "NODEJS_HOME": str(tmp_path / "node-home-without-node"),
            },
        )


@pytest.mark.parametrize("agent", ("codex", "claude"))
def test_native_or_non_codex_launchers_do_not_require_node(
    tmp_path: Path, agent: str
) -> None:
    launcher = _executable(tmp_path / agent)
    environment = {"PATH": str(tmp_path / "path-without-node")}

    prepare_agent_runtime_environment(agent, str(launcher), environment)

    assert environment == {"PATH": str(tmp_path / "path-without-node")}
