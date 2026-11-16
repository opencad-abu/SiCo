from pathlib import Path

import pytest

from aivworkbench.artifact_paths import (
    PathContractError,
    path_has_symlink_component,
    resolve_rooted_locator,
    trusted_root,
    validate_output_path,
    validate_relative_path,
)


def test_trusted_root_rejects_root_ancestor_and_intermediate_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert trusted_root(root) == root.resolve()

    root_alias = tmp_path / "root-alias"
    root_alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(PathContractError):
        trusted_root(root_alias)

    parent_alias = tmp_path / "parent-alias"
    parent_alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(PathContractError):
        trusted_root(parent_alias / "root")


@pytest.mark.parametrize("value", ["", ".", "a/./b", "a//b", "a/../b", "../x", "/x", "C:foo", "a\\b"])
def test_relative_path_contract_rejects_aliases(value: str) -> None:
    with pytest.raises(PathContractError):
        validate_relative_path(value)


def test_rooted_locator_validates_kind_and_all_components(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "dir").mkdir()
    (root / "dir" / "item.txt").write_text("ok", encoding="utf-8")
    assert resolve_rooted_locator(root, "dir/item.txt", kind="file").name == "item.txt"
    assert resolve_rooted_locator(root, "dir", kind="directory").name == "dir"
    with pytest.raises(PathContractError):
        resolve_rooted_locator(root, "dir", kind="file")

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathContractError):
        resolve_rooted_locator(root, "linked/missing", must_exist=False)


def test_output_path_allows_missing_leaf_but_rejects_escape_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "payload"
    root.mkdir()
    output = validate_output_path(root, root / "new" / "result", kind="file")
    assert output == root / "new" / "result"
    with pytest.raises(PathContractError):
        validate_output_path(root, root / ".." / "outside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathContractError):
        validate_output_path(root, root / "linked" / "result")


def test_output_path_checks_existing_type_and_io_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "file").write_text("x", encoding="utf-8")
    with pytest.raises(PathContractError):
        validate_output_path(root, "file", kind="directory", must_exist=True)
    with pytest.raises(PathContractError):
        resolve_rooted_locator(root, "missing", kind="file")

    original = Path.resolve

    def broken(self: Path, *args, **kwargs):
        if self == root:
            raise OSError("resolve failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", broken)
    with pytest.raises(PathContractError):
        trusted_root(root)


def test_symlink_probe_marks_outside_candidate_unsafe(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    assert path_has_symlink_component(root, tmp_path / "other")
