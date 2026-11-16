"""Terminal build contract regressions."""

from __future__ import annotations

from terminal_fixtures import (
    PYQT_ROOT,
    TERMINAL_CMAKE,
)


def test_cmake_installs_pyqt_terminal_as_the_default_frontend() -> None:
    source = TERMINAL_CMAKE.read_text(encoding="utf-8")

    install_assistant = source.index(
        'install(PROGRAMS "${_ai_source_root}/bin/aiassistant" DESTINATION bin)'
    )
    install_dispatcher = source.index(
        'install(PROGRAMS "${CMAKE_CURRENT_SOURCE_DIR}/scripts/sico-ai-terminal-dispatcher.sh"'
    )
    install_pinyin = source.index(
        'install(PROGRAMS "${_ai_source_root}/bin/sico-ai-pinyin"'
    )
    install_launcher = source.index('install(PROGRAMS "${_pyqt_source_root}/sico-ai-terminal-pyqt"')
    install_package = source.index("DESTINATION python/cad_ai_terminal", install_launcher)
    install_binding = source.index("DESTINATION python RENAME QTermWidget.abi3.so")

    assert "CAD_AI_INSTALL_PYQT5_TERMINAL" not in source
    assert install_assistant < install_dispatcher
    assert (
        install_dispatcher < install_pinyin < install_launcher < install_package < install_binding
    )
    assert "SICO_AI_BUILD_LEGACY_CPP_TERMINAL" not in source
    assert "cad-ai-terminal-cpp" not in source
    assert "enable_language(CXX)" not in source


def test_cmake_installs_only_the_qtermwidget_runtime() -> None:
    source = TERMINAL_CMAKE.read_text(encoding="utf-8")

    assert "scripts/build_qtermwidget5_pyqt.sh" not in source
    assert "scripts/build_pyqt5_runtime.sh" not in source
    assert 'PATTERN ".pytest_cache" EXCLUDE' in source
    assert 'PATTERN ".ruff_cache" EXCLUDE' in source
    assert "DESTINATION python RENAME QTermWidget.abi3.so" in source
    assert "DESTINATION lib RENAME libqtermwidget5.so.1" in source
    assert "DESTINATION share/qtermwidget5/pyqt" not in source


def test_binding_helper_supports_production_python_and_installed_patch_layout() -> None:
    helper = (PYQT_ROOT.parent / "scripts" / "build_qtermwidget5_pyqt.sh").read_text(
        encoding="utf-8"
    ) + (PYQT_ROOT.parent / "scripts" / "configure_qtermwidget_pyqt.sh").read_text(
        encoding="utf-8"
    )

    assert "python=${SICO_PYTHON-}" in helper
    assert 'python="$SICO_PYTHON_ROOT/bin/python3"' in helper
    assert "python=${SICO_AI_PYQT_PYTHON-${SICO_AI_PYQT5_PYTHON-}}" in helper
    assert (
        "python_library_path="
        "${SICO_AI_PYTHON_LIBRARY_PATH-${SICO_SYSTEM_LD_LIBRARY_PATH-}}" in helper
    )
    assert 'screen_lines_patch="$script_dir/qtermwidget-1.4.0-pyqt-screen-lines.patch"' in helper
    assert 'python_prefix=$(realpath -m -- "$(dirname -- "$python")/..")' in helper
    assert 'python_include="$python_prefix/include/python$python_version"' in helper
    assert "QMAKE_LFLAGS_RPATH = -Wl,-rpath," in helper
    assert "QMAKE_RPATHDIR += '\\$ORIGIN/../lib'" in helper
    assert "session-shutdown patch" in helper


def test_pyqt_runtime_helper_is_private_and_stages_sip_support() -> None:
    helper = (PYQT_ROOT.parent / "scripts" / "build_pyqt5_runtime.sh").read_text(encoding="utf-8")

    assert "build_pyqt5_runtime.sh PyQt5-5.15.11.tar.gz" in helper
    assert 'build_parent=$(realpath -m -- "$(pwd -P)/.cad")' in helper
    assert "build root must be below CWD/.cad" in helper
    assert "install directory must be outside production site-packages" in helper
    assert 'source_sip_modules=("$source_pyqt_package"/sip*.so)' in helper
    assert 'cp -- "${source_sip_modules[0]}" "$pyqt_package/"' in helper
    assert 'PYTHONPATH="$install_root"' in helper
    assert "QApplication([])" in helper
    assert "QT_QPA_PLATFORM=offscreen" in helper


def test_qterm_helper_uses_production_pyqt_and_relocates_host_tools() -> None:
    helper = (PYQT_ROOT.parent / "scripts" / "build_qtermwidget5_pyqt.sh").read_text(
        encoding="utf-8"
    ) + (PYQT_ROOT.parent / "scripts" / "configure_qtermwidget_pyqt.sh").read_text(
        encoding="utf-8"
    )

    assert "CAD_AI_PYQT5_RUNTIME" not in helper
    assert "production PyQt5 Qt runtime" in helper
    assert "SICO_AI_PYQT_CC" in helper
    assert "SICO_AI_PYQT_CXX" in helper
    assert "SICO_AI_PYQT_COMPILER_LIBRARY_PATH" in helper
    assert "SICO_AI_PYQT_CXXFLAGS" in helper
    assert "SICO_AI_PYQT_LFLAGS" in helper
    assert "installed extension production PyQt5 smoke failed" in helper
    assert 'SICO_AI_QTERMWIDGET_DATA_PATH="$resource_root"' in helper
    assert "release ELF contains a build-machine absolute path" in helper
    assert 'build_parent=$(realpath -m -- "$(pwd -P)/.cad")' in helper
    assert "build root must be below CWD/.cad" in helper
    assert "HostBinaries=$qt_host_bin_dir" in helper
    assert "sip_abi_version=12.11" in helper
    assert '--abi-version "$sip_abi_version"' in helper
    assert "get_source_version_range(12)" in helper
    assert "sipExportModule\\(&sipModuleAPI_QTermWidget, 12, 11, 0\\)" in helper
    assert "CAD_AI_QTERMWIDGET_SIP_ABI=$sip_abi_version" in helper


def test_release_verifier_enforces_qtermwidget_sip_abi_12_11() -> None:
    verifier = (PYQT_ROOT.parent / "scripts" / "verify_release_abi.sh").read_text(encoding="utf-8")

    assert "sip_abi_version=12.11" in verifier
    assert "CAD_AI_QTERMWIDGET_SIP_ABI=$sip_abi_version" in verifier
    assert "does not declare required SIP ABI" in verifier


def test_qtermwidget_source_build_rejects_partially_applied_relocation_patch() -> None:
    helper = (PYQT_ROOT.parent / "scripts" / "build_qtermwidget5.sh").read_text(encoding="utf-8")
    patch = (PYQT_ROOT.parent / "patches" / "qtermwidget-1.4.0-relocatable-data.patch").read_text(
        encoding="utf-8"
    )

    assert 'qgetenv("CAD_AI_QTERMWIDGET_DATA_PATH")' not in patch
    assert 'qgetenv("SICO_AI_QTERMWIDGET_DATA_PATH")' in patch
    assert 'get_qtermwidget_data_dir(QLatin1String("kb-layouts"))' in patch
    assert 'get_qtermwidget_data_dir(QLatin1String("color-schemes"))' in patch
    assert "relocatable-data patch was only partially applied" in helper
    assert "QLatin1String\\((KB_LAYOUT_DIR|COLORSCHEMES_DIR)\\)" in helper
    assert 'build_parent=$(realpath -m -- "$(pwd -P)/.cad")' in helper
    assert "build root must be below CWD/.cad" in helper
    assert "${TMPDIR:-/tmp}" not in helper


def test_cmake_requires_and_installs_pinned_native_codex_runtime() -> None:
    source = TERMINAL_CMAKE.read_text(encoding="utf-8")
    lock = TERMINAL_CMAKE.parent / "codex-runtime.lock"
    assert lock.is_file()
    assert "SICO_AI_CODEX_EXECUTABLE" in source
    assert "cad_ai_manifest_value(_codex_expected_size binary_size)" in source
    assert "cad_ai_manifest_value(_codex_expected_version cli_version)" in source
    assert 'file(SIZE "${_codex_executable}" _codex_actual_size)' in source
    assert 'file(SHA256 "${_codex_executable}" _codex_actual_sha256)' in source
    assert 'DESTINATION "runtime/codex/${_codex_target}/bin"' in source
    assert "RENAME codex" in source
    assert "RENAME MANIFEST.txt" in source


def test_pyqt_docs_require_the_session_shutdown_qtermwidget_patch() -> None:
    documentation = (PYQT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "qtermwidget-1.4.0-session-shutdown.patch" in documentation
    assert "rejects an" in documentation and "unpatched prefix" in documentation
