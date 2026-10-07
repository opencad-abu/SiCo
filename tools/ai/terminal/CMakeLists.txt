include("${CMAKE_CURRENT_LIST_DIR}/cmake/sico-options.cmake")

cmake_minimum_required(VERSION 3.16)
project(cad_ai_terminal VERSION 0.1.0 LANGUAGES NONE)

# Default installation stages native dependencies only. Application releases
# require qualified native artifacts and deploy/package_runtime_release.py.
option(SICO_AI_DEVELOPMENT_STAGE
    "Stage internal development sources (never a deployment payload)" OFF)

set(CMAKE_FIND_USE_PACKAGE_REGISTRY OFF)
set(CMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY OFF)

set(SICO_AI_QTERMWIDGET_ROOT "/software/pkgs/qt/5/qtermwidget" CACHE PATH
    "QTermWidget 1.4 installation prefix")
set(_qtermwidget_share "${SICO_AI_QTERMWIDGET_ROOT}/share/qtermwidget5")
set(SICO_AI_QTERMWIDGET_PYTHON_MODULE "" CACHE FILEPATH
    "Platform QTermWidget.abi3.so built for the production PyQt5 runtime")
set(SICO_AI_CODEX_EXECUTABLE "" CACHE FILEPATH
    "Pinned x86_64-unknown-linux-musl Codex executable; defaults to the source-tree tools/ai/runtime/codex runtime")
set(SICO_AI_SKILL_CONTEXT "" CACHE FILEPATH
    "Protected Virtuoso .cxt built by deploy/build_skill_context.py")
set(SICO_PDK_WORKER_CONTEXT "" CACHE FILEPATH
    "Protected cadPdkWork.cxt built by deploy/build_pdk_worker_context.py")
set(SICO_AI_NATIVE_ENTRY_DIR "" CACHE PATH
    "Private build output containing native sico-ai launchers")
set(SICO_AI_NATIVE_PYTHON_DIR "" CACHE PATH
    "Private Cython output containing the source-free Python runtime")

set(_sico_source_root "${CMAKE_CURRENT_SOURCE_DIR}/../../..")
set(_cad_source_root "${_sico_source_root}")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${_sico_source_root}/deploy/sico_resource_inputs.py"
        "${CMAKE_CURRENT_BINARY_DIR}/sico-resources.cmake"
    RESULT_VARIABLE _sico_resource_result)
if(NOT _sico_resource_result EQUAL 0)
    message(FATAL_ERROR "SiCo resource inputs failed hash validation")
endif()
include("${CMAKE_CURRENT_BINARY_DIR}/sico-resources.cmake")
execute_process(
    COMMAND "${Python3_EXECUTABLE}" "${_sico_source_root}/deploy/flow_defaults_inputs.py"
        "${CMAKE_CURRENT_BINARY_DIR}/flow-defaults.cmake"
    RESULT_VARIABLE _flow_defaults_result)
if(NOT _flow_defaults_result EQUAL 0)
    message(FATAL_ERROR "Flow defaults failed input validation")
endif()
include("${CMAKE_CURRENT_BINARY_DIR}/flow-defaults.cmake")

set(_ai_source_root "${CMAKE_CURRENT_SOURCE_DIR}/..")
# Runtime metadata for stable tool libraries; process PDKs remain site supplied.
install(FILES
    "${_ai_source_root}/share/pdk-data/analogLib.json.gz"
    "${_ai_source_root}/share/pdk-data/basic.json.gz"
    DESTINATION share/pdk-data)
include("${CMAKE_CURRENT_SOURCE_DIR}/ripgrep-runtime.cmake")
set(_codex_runtime_manifest
    "${CMAKE_CURRENT_SOURCE_DIR}/codex-runtime.lock")
if(NOT EXISTS "${_codex_runtime_manifest}")
    message(FATAL_ERROR
        "Pinned Codex runtime manifest is missing: ${_codex_runtime_manifest}")
endif()

function(cad_ai_manifest_value output key)
    file(STRINGS "${_codex_runtime_manifest}" _lines
        REGEX "^${key}=.*$")
    list(LENGTH _lines _line_count)
    if(NOT _line_count EQUAL 1)
        message(FATAL_ERROR
            "Pinned Codex runtime manifest must contain exactly one ${key}")
    endif()
    list(GET _lines 0 _line)
    string(REGEX REPLACE "^[^=]*=" "" _value "${_line}")
    set("${output}" "${_value}" PARENT_SCOPE)
endfunction()

cad_ai_manifest_value(_codex_format format)
cad_ai_manifest_value(_codex_target target)
cad_ai_manifest_value(_codex_expected_size binary_size)
cad_ai_manifest_value(_codex_expected_sha256 binary_sha256)
cad_ai_manifest_value(_codex_expected_version cli_version)
if(NOT _codex_format STREQUAL "cad-ai.codex-runtime.v1")
    message(FATAL_ERROR
        "Pinned Codex runtime manifest has an unsupported format: ${_codex_format}")
endif()
if(NOT _codex_target STREQUAL "x86_64-unknown-linux-musl")
    message(FATAL_ERROR
        "Pinned Codex runtime manifest has an unsupported target: ${_codex_target}")
endif()
if(NOT _codex_expected_size MATCHES "^[1-9][0-9]*$")
    message(FATAL_ERROR "Pinned Codex executable size is invalid")
endif()
string(LENGTH "${_codex_expected_sha256}" _codex_sha256_length)
if(NOT _codex_sha256_length EQUAL 64
        OR NOT _codex_expected_sha256 MATCHES "^[0-9a-f]+$")
    message(FATAL_ERROR "Pinned Codex executable SHA256 is invalid")
endif()
if(NOT _codex_expected_version MATCHES "^codex-cli [0-9]+[.][0-9]+[.][0-9]+$")
    message(FATAL_ERROR "Pinned Codex executable version is invalid")
endif()
if(NOT SICO_AI_CODEX_EXECUTABLE)
    set(SICO_AI_CODEX_EXECUTABLE
        "${_ai_source_root}/runtime/codex/${_codex_target}/bin/codex")
endif()
get_filename_component(_codex_executable
    "${SICO_AI_CODEX_EXECUTABLE}" REALPATH)
if(NOT EXISTS "${_codex_executable}" OR IS_DIRECTORY "${_codex_executable}")
    message(FATAL_ERROR
        "Codex executable is missing: ${SICO_AI_CODEX_EXECUTABLE}")
endif()
file(SIZE "${_codex_executable}" _codex_actual_size)
if(NOT _codex_actual_size STREQUAL _codex_expected_size)
    message(FATAL_ERROR
        "Codex executable size does not match the pinned runtime manifest")
endif()
file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/codex-probe-home")
file(SHA256 "${_codex_executable}" _codex_actual_sha256)
if(NOT _codex_actual_sha256 STREQUAL _codex_expected_sha256)
    message(FATAL_ERROR
        "Codex executable does not match the pinned runtime manifest")
endif()
execute_process(
    COMMAND "${CMAKE_COMMAND}" -E env
        "HOME=${CMAKE_CURRENT_BINARY_DIR}/codex-probe-home"
        "PATH=/usr/bin:/bin"
        "${_codex_executable}" --version
    RESULT_VARIABLE _codex_version_status
    OUTPUT_VARIABLE _codex_version
    ERROR_VARIABLE _codex_version_error
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_STRIP_TRAILING_WHITESPACE
    TIMEOUT 10
)
if(NOT _codex_version_status EQUAL 0
        OR NOT _codex_version STREQUAL _codex_expected_version)
    message(FATAL_ERROR
        "Pinned Codex executable failed its version probe: ${_codex_version_error}")
endif()

# Code-mode tools start this companion beside the main executable.
get_filename_component(_codex_bin_dir "${_codex_executable}" DIRECTORY)
set(_codex_code_host "${_codex_bin_dir}/codex-code-mode-host")
cad_ai_manifest_value(_codex_code_host_size code_host_binary_size)
cad_ai_manifest_value(_codex_code_host_sha256 code_host_binary_sha256)
if(NOT EXISTS "${_codex_code_host}" OR IS_DIRECTORY "${_codex_code_host}"
        OR IS_SYMLINK "${_codex_code_host}")
    message(FATAL_ERROR "Pinned Codex code-mode host is missing: ${_codex_code_host}")
endif()
file(SIZE "${_codex_code_host}" _codex_code_host_actual_size)
file(SHA256 "${_codex_code_host}" _codex_code_host_actual_sha256)
if(NOT _codex_code_host_actual_size STREQUAL _codex_code_host_size
        OR NOT _codex_code_host_actual_sha256 STREQUAL _codex_code_host_sha256)
    message(FATAL_ERROR "Codex code-mode host does not match the pinned runtime manifest")
endif()

if(SICO_AI_DEVELOPMENT_STAGE)
    install(FILES "${_ai_source_root}/README.md" DESTINATION .)
    install(PROGRAMS "${_ai_source_root}/bin/aiassistant" DESTINATION bin)
endif()
install(PROGRAMS "${_codex_executable}"
    DESTINATION "runtime/codex/${_codex_target}/bin"
    RENAME codex)
install(PROGRAMS "${_codex_code_host}"
    DESTINATION "runtime/codex/${_codex_target}/bin")
install(FILES "${_codex_runtime_manifest}"
    DESTINATION "runtime/codex/${_codex_target}"
    RENAME MANIFEST.txt)
if(SICO_PDK_WORKER_CONTEXT)
    get_filename_component(_cad_pdk_context "${SICO_PDK_WORKER_CONTEXT}" REALPATH)
    get_filename_component(_cad_pdk_context_name "${_cad_pdk_context}" NAME)
    if(NOT EXISTS "${_cad_pdk_context}" OR IS_DIRECTORY "${_cad_pdk_context}"
            OR NOT _cad_pdk_context_name STREQUAL "cadPdkWork.cxt")
        message(FATAL_ERROR "SICO_PDK_WORKER_CONTEXT must name a compiled cadPdkWork.cxt")
    endif()
    install(FILES "${_cad_pdk_context}" DESTINATION context)
endif()

if(SICO_AI_SKILL_CONTEXT)
    get_filename_component(_cad_ai_skill_context "${SICO_AI_SKILL_CONTEXT}" REALPATH)
    if(NOT EXISTS "${_cad_ai_skill_context}" OR IS_DIRECTORY "${_cad_ai_skill_context}")
        message(FATAL_ERROR "SICO_AI_SKILL_CONTEXT is not a regular file")
    endif()
    get_filename_component(_cad_ai_skill_context_name "${_cad_ai_skill_context}" NAME)
    string(LENGTH "${_cad_ai_skill_context_name}" _cad_ai_skill_context_name_length)
    if(NOT _cad_ai_skill_context_name MATCHES "^[A-Za-z][A-Za-z0-9_]*[.]cxt$"
            OR _cad_ai_skill_context_name_length GREATER 15)
        message(FATAL_ERROR "SICO_AI_SKILL_CONTEXT must be an alphanumeric .cxt file of at most 11 characters")
    endif()
    file(SIZE "${_cad_ai_skill_context}" _cad_ai_skill_context_size)
    if(_cad_ai_skill_context_size LESS 64)
        message(FATAL_ERROR "SICO_AI_SKILL_CONTEXT is too small to be a Virtuoso context")
    endif()
    install(FILES "${_cad_ai_skill_context}"
        DESTINATION context RENAME "${_cad_ai_skill_context_name}")
endif()
if(SICO_AI_DEVELOPMENT_STAGE)
install(PROGRAMS "${_ai_source_root}/python/sico-ai" DESTINATION python)
install(DIRECTORY "${_ai_source_root}/python/cadai" DESTINATION python
    FILES_MATCHING
        PATTERN "*.py"
        PATTERN "__pycache__" EXCLUDE
)
install(DIRECTORY "${_ai_source_root}/skill/" DESTINATION skill
    FILES_MATCHING PATTERN "*.il"
)
install(DIRECTORY "${_ai_source_root}/skill++/" DESTINATION skill++
    FILES_MATCHING PATTERN "*.ils"
)
install(DIRECTORY "${_ai_source_root}/skills/" DESTINATION skills
    PATTERN "__pycache__" EXCLUDE
    PATTERN ".pytest_cache" EXCLUDE
    PATTERN ".ruff_cache" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
)
set(_skill_reference_root "${_ai_source_root}/reference/cadence-skill")
set(_skill_reference_files
    "${_skill_reference_root}/skill_api.sqlite3"
    "${_skill_reference_root}/skill_api.json.gz"
    "${_skill_reference_root}/README.md"
    "${_skill_reference_root}/NOTICE.md"
    "${_skill_reference_root}/build_reference.py"
)
foreach(_reference_file IN LISTS _skill_reference_files)
    if(NOT EXISTS "${_reference_file}")
        message(FATAL_ERROR "Required Cadence SKILL reference file is missing: ${_reference_file}")
    endif()
endforeach()
install(FILES ${_skill_reference_files} DESTINATION reference/cadence-skill)
install(FILES
    "${_ai_source_root}/reference/circuit_templates/catalogs/builtin-passive-v1.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-custom-gates45-r6.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-ams_emir-r6.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-ams_gui-r6.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-aot-r6.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-custom_ic-r6-001.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-custom_ic-r6-002.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-custom_ic-r6-003.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-custom_ic-r6-004.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/cadence-training-dot-r6.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/ldo-ip-r6-20260916.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_39d442b40e29afcc248cd0f47119befd4dea665a972904956f634e97ee46a9e9.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_44ac321516617f7766c9d3fbfde4139f1c27891ea090260f753ea5d61bbc50d3.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_486fbb535a7e7cb45d3604c2f8a7f433543b4c7516050550086f1073987bf844.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_bfd1ef3915dd785a7c25cb144c821651404e2dd0f6e612f5b47444ed2ed23e3d.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_de9e1598ad03b69d5214b5eb61c89df96f7a3c8e21fae6b35ae6d587f31130ab.sqlite3"
    "${_ai_source_root}/reference/circuit_templates/catalogs/tpl_edd1e9ade4319f9b651ae3a60a216ac49ef023c6849e8f09b484f8f1c689d587.sqlite3"
    DESTINATION reference/circuit_templates/catalogs
)
install(FILES
    "${_ai_source_root}/reference/circuit_templates/README.md"
    "${_ai_source_root}/reference/circuit_templates/build_builtin.py"
    DESTINATION reference/circuit_templates
)
install(FILES
    "${_ai_source_root}/reference/circuit_templates/training/README.md"
    "${_ai_source_root}/reference/circuit_templates/training/NOTICE.md"
    "${_ai_source_root}/reference/circuit_templates/training/SOURCES.json"
    "${_ai_source_root}/reference/circuit_templates/training/ams_emir.json"
    "${_ai_source_root}/reference/circuit_templates/training/ams_gui.json"
    "${_ai_source_root}/reference/circuit_templates/training/aot.json"
    "${_ai_source_root}/reference/circuit_templates/training/custom_ic.json"
    "${_ai_source_root}/reference/circuit_templates/training/dot.json"
    "${_ai_source_root}/reference/circuit_templates/training/build_catalogs.py"
    DESTINATION reference/circuit_templates/training
)
install(FILES
    "${_ai_source_root}/reference/circuit_templates/custom_gates/README.md"
    "${_ai_source_root}/reference/circuit_templates/custom_gates/NOTICE.md"
    "${_ai_source_root}/reference/circuit_templates/custom_gates/SOURCES.json"
    "${_ai_source_root}/reference/circuit_templates/custom_gates/build_catalog.py"
    DESTINATION reference/circuit_templates/custom_gates
)
install(DIRECTORY "${_ai_source_root}/docs/" DESTINATION docs)
endif()
if(SICO_AI_NATIVE_ENTRY_DIR)
    foreach(_native_entry sico-ai aiassistant sico sico-mcp sico-ai-pinyin
                          sico-ai-terminal sico-ai-terminal-pyqt)
        set(_native_path "${SICO_AI_NATIVE_ENTRY_DIR}/${_native_entry}")
        if(NOT EXISTS "${_native_path}" OR IS_SYMLINK "${_native_path}")
            message(FATAL_ERROR "Native CAD launcher is missing: ${_native_path}")
        endif()
        install(PROGRAMS "${_native_path}" DESTINATION bin)
    endforeach()
endif()
if(SICO_AI_NATIVE_PYTHON_DIR)
    get_filename_component(_native_python_root "${SICO_AI_NATIVE_PYTHON_DIR}" REALPATH)
    if(NOT EXISTS "${_native_python_root}" OR NOT IS_DIRECTORY "${_native_python_root}")
        message(FATAL_ERROR "Native Python runtime directory is missing: ${SICO_AI_NATIVE_PYTHON_DIR}")
    endif()
    file(GLOB_RECURSE _native_python_files RELATIVE "${_native_python_root}"
        "${_native_python_root}/*.so")
    if(NOT _native_python_files)
        message(FATAL_ERROR "Native Python runtime contains no extension modules: ${_native_python_root}")
    endif()
    file(GLOB_RECURSE _native_python_all RELATIVE "${_native_python_root}"
        "${_native_python_root}/*")
    foreach(_native_python_file IN LISTS _native_python_all)
        if(IS_DIRECTORY "${_native_python_root}/${_native_python_file}")
            continue()
        endif()
        if(NOT _native_python_file MATCHES "^[A-Za-z0-9_+./-]+[.]so$")
            message(FATAL_ERROR "Native Python runtime contains a non-native file: ${_native_python_file}")
        endif()
        if(IS_SYMLINK "${_native_python_root}/${_native_python_file}")
            message(FATAL_ERROR "Native Python runtime contains a symlink: ${_native_python_file}")
        endif()
    endforeach()
    foreach(_native_python_file IN LISTS _native_python_files)
        get_filename_component(_native_python_dir "${_native_python_file}" DIRECTORY)
        install(FILES "${_native_python_root}/${_native_python_file}"
            DESTINATION "python/${_native_python_dir}")
    endforeach()
endif()
install(FILES
    "${_ai_source_root}/LICENSES/openai-codex-Apache-2.0.txt"
    "${_ai_source_root}/LICENSES/openai-codex-NOTICE.txt"
    "${_ai_source_root}/LICENSES/ripgrep-LICENSE-MIT.txt"
    "${_ai_source_root}/LICENSES/ripgrep-UNLICENSE.txt"
    "${_ai_source_root}/LICENSES/ripgrep-NOTICE.md"
    "${_ai_source_root}/LICENSES/ripgrep-musl-COPYRIGHT.txt"
    "${_ai_source_root}/LICENSES/ripgrep-PCRE2-LICENCE.txt"
    DESTINATION LICENSES)

set(_pyqt_source_root "${CMAKE_CURRENT_SOURCE_DIR}/pyqt")
if(SICO_AI_DEVELOPMENT_STAGE)
foreach(_pyqt_required
        "${_pyqt_source_root}/sico-ai-terminal-pyqt"
        "${_pyqt_source_root}/cad_ai_terminal/__init__.py"
        "${_pyqt_source_root}/cad_ai_terminal/main.py"
        "${_pyqt_source_root}/cad_ai_terminal/protocol.py"
        "${_pyqt_source_root}/cad_ai_terminal/qt_frontend.py"
        "${_pyqt_source_root}/cad_ai_terminal/terminal_session.py"
        "${_pyqt_source_root}/cad_ai_terminal/appearance.py"
        "${_pyqt_source_root}/cad_ai_terminal/control_channel.py"
        "${_pyqt_source_root}/cad_ai_terminal/input_behavior.py"
        "${_pyqt_source_root}/cad_ai_terminal/resources.py"
        "${_pyqt_source_root}/cad_ai_terminal/session_context.py"
        "${_pyqt_source_root}/cad_ai_terminal/shell_environment.py"
        "${_pyqt_source_root}/cad_ai_terminal/tab_renamer.py"
        "${_pyqt_source_root}/cad_ai_terminal/terminal_widget.py"
        "${_pyqt_source_root}/cad_ai_terminal/terminal_window.py"
        "${_pyqt_source_root}/cad_ai_terminal/output_capture.py"
        "${_pyqt_source_root}/cad_ai_terminal/toolbar.py"
        "${_pyqt_source_root}/cad_ai_terminal/window_close.py"
        "${_pyqt_source_root}/cad_ai_terminal/window_shutdown.py"
        "${_pyqt_source_root}/README.md")
    if(NOT EXISTS "${_pyqt_required}")
        message(FATAL_ERROR
            "PyQt5 terminal source file is missing: ${_pyqt_required}")
    endif()
endforeach()
endif()

if(NOT SICO_AI_QTERMWIDGET_PYTHON_MODULE)
    message(FATAL_ERROR
        "SICO_AI_QTERMWIDGET_PYTHON_MODULE must name the platform QTermWidget.abi3.so")
endif()
get_filename_component(_qtermwidget_python_module
    "${SICO_AI_QTERMWIDGET_PYTHON_MODULE}" REALPATH)
if(NOT EXISTS "${_qtermwidget_python_module}")
    message(FATAL_ERROR
        "QTermWidget Python module is missing: ${SICO_AI_QTERMWIDGET_PYTHON_MODULE}")
endif()

set(_qtermwidget_library "${SICO_AI_QTERMWIDGET_ROOT}/lib/libqtermwidget5.so.1")
if(NOT EXISTS "${_qtermwidget_library}")
    set(_qtermwidget_library "${SICO_AI_QTERMWIDGET_ROOT}/lib64/libqtermwidget5.so.1")
endif()
get_filename_component(_qtermwidget_library "${_qtermwidget_library}" REALPATH)
if(NOT EXISTS "${_qtermwidget_library}")
    message(FATAL_ERROR
        "QTermWidget runtime library is missing below: ${SICO_AI_QTERMWIDGET_ROOT}")
endif()

foreach(_required_resource
        "${_qtermwidget_share}/kb-layouts/default.keytab"
        "${_qtermwidget_share}/color-schemes/BreezeModified.colorscheme")
    if(NOT EXISTS "${_required_resource}")
        message(FATAL_ERROR "Required QTermWidget resource is missing: ${_required_resource}")
    endif()
endforeach()

if(SICO_AI_DEVELOPMENT_STAGE)
install(PROGRAMS "${CMAKE_CURRENT_SOURCE_DIR}/scripts/sico-ai-terminal-dispatcher.sh"
    DESTINATION bin RENAME sico-ai-terminal)
install(PROGRAMS "${_ai_source_root}/bin/sico-ai-pinyin"
    DESTINATION bin RENAME sico-ai-pinyin)
install(PROGRAMS "${_pyqt_source_root}/sico-ai-terminal-pyqt"
    DESTINATION bin)
install(DIRECTORY "${_pyqt_source_root}/cad_ai_terminal/"
    DESTINATION python/cad_ai_terminal
    FILES_MATCHING
        PATTERN "*.py"
        PATTERN "__pycache__" EXCLUDE
        PATTERN "*.pyc" EXCLUDE)
endif()
install(FILES "${_qtermwidget_python_module}"
    DESTINATION python RENAME QTermWidget.abi3.so)
install(FILES "${_qtermwidget_library}"
    DESTINATION lib RENAME libqtermwidget5.so.1)
if(SICO_AI_DEVELOPMENT_STAGE)
install(FILES "${_pyqt_source_root}/README.md"
    DESTINATION docs RENAME pyqt-terminal.md)
install(FILES "${_ai_source_root}/docs/PINYIN_INPUT.md"
    DESTINATION docs RENAME pinyin-wrapper.md)
endif()
