"""Synchronous provider selection and invocation contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from cadview import Catalog, CatalogError, CatalogLibrary, CatalogResult, LibraryManager
from cadview_manager_fixtures import _cds


def test_manager_loads_filesystem_provider_with_diagnostics(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    with LibraryManager() as manager:
        result = manager.load(cds, provider="filesystem")

    assert isinstance(result, CatalogResult)
    assert result.catalog.cds_library_file == cds.resolve()
    assert result.provider == "filesystem"
    assert result.authoritative is False
    assert result.diagnostics.cds_library_file == cds.resolve()


def test_manager_rejects_missing_and_unknown_inputs(tmp_path: Path) -> None:
    manager = LibraryManager()
    try:
        with pytest.raises(CatalogError, match="cannot access cds.lib"):
            manager.load(tmp_path / "missing.cds.lib", provider="filesystem")
        with pytest.raises(CatalogError, match="unknown catalog provider"):
            manager.load(_cds(tmp_path), provider="oa_magic")
    finally:
        manager.close()


def test_manager_custom_provider_is_qt_free_and_preserves_result(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    calls: list[tuple[Path, dict[str, object]]] = []

    def provider(path: Path, *, environ=None) -> Catalog:
        calls.append((path, {"environ": environ}))
        return Catalog(path, (CatalogLibrary("custom", path.parent, False),), provider="custom")

    with LibraryManager(providers={"custom": provider}) as manager:
        result = manager.load(cds, provider="custom", environ={"PROJECT": "x"})

    assert result.catalog.library("custom") is not None
    assert calls == [(cds.resolve(), {"environ": {"PROJECT": "x"}})]


def test_custom_provider_receives_only_supported_complete_context(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    calls: list[tuple[str, Path | None]] = []

    def provider(
        path: Path,
        *,
        executable: str,
        script: Path | None,
    ) -> Catalog:
        calls.append((executable, script))
        return Catalog(path, (), provider="custom")

    script = tmp_path / "catalog.il"
    with LibraryManager(providers={"custom": provider}) as manager:
        manager.load(
            cds,
            provider="custom",
            executable="site-dbAccess",
            script=script,
        )

    assert calls == [("site-dbAccess", script)]


def test_manager_forwards_output_callback_to_supporting_provider(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    received: list[str] = []

    def provider(path: Path, *, output_callback=None) -> Catalog:
        output_callback("PDK initialized\n")
        return Catalog(path, (), provider="custom")

    with LibraryManager(providers={"custom": provider}) as manager:
        manager.load(cds, provider="custom", output_callback=received.append)

    assert received == ["PDK initialized\n"]


def test_positional_only_provider_parameters_are_not_passed_as_keywords(
    tmp_path: Path,
) -> None:
    cds = _cds(tmp_path)

    def provider(path: Path, environ=None, /) -> Catalog:
        assert environ is None
        return Catalog(path, (), provider="positional")

    with LibraryManager(providers={"positional": provider}) as manager:
        assert manager.load(
            cds,
            provider="positional",
            environ={"PROJECT": "ignored"},
        ).provider == "positional"


def test_provider_type_error_is_not_hidden_by_a_retry(tmp_path: Path) -> None:
    cds = _cds(tmp_path)
    calls = 0

    def provider(_path: Path, *, environ=None) -> Catalog:
        nonlocal calls
        calls += 1
        raise TypeError("provider implementation failed")

    with LibraryManager(providers={"broken": provider}) as manager:
        with pytest.raises(TypeError, match="implementation failed"):
            manager.load(cds, provider="broken")

    assert calls == 1


def test_callable_provider_and_public_registration(tmp_path: Path) -> None:
    cds = _cds(tmp_path)

    def provider(path: Path) -> Catalog:
        return Catalog(path, (), provider="callable")

    with LibraryManager() as manager:
        assert manager.load(cds, provider=provider).provider == "callable"
        manager.register_provider("site", provider)
        assert manager.load(cds, provider="site").provider == "callable"


def test_register_provider_validates_input_and_closed_state(tmp_path: Path) -> None:
    manager = LibraryManager()
    with pytest.raises(ValueError, match="name"):
        manager.register_provider("", lambda path: Catalog(path))
    with pytest.raises(TypeError, match="callable"):
        manager.register_provider("invalid", object())  # type: ignore[arg-type]
    manager.close()
    with pytest.raises(RuntimeError, match="closed"):
        manager.register_provider("site", lambda path: Catalog(path))


@pytest.mark.parametrize(
    ("providers", "error", "message"),
    [
        ({"": lambda path: Catalog(path)}, ValueError, "name"),
        ({"invalid": object()}, TypeError, "callable"),
    ],
)
def test_constructor_validates_custom_providers(
    providers: dict[str, object],
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        LibraryManager(providers=providers)  # type: ignore[arg-type]
