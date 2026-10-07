# Shared static rg for source checkouts and all production release variants.
set(_rg_root "${CMAKE_CURRENT_SOURCE_DIR}/../runtime/ripgrep/x86_64-unknown-linux-musl")
set(_rg_lock "${CMAKE_CURRENT_SOURCE_DIR}/ripgrep-runtime.lock")
execute_process(
    COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/scripts/verify_ripgrep_runtime.sh"
        "${CMAKE_CURRENT_SOURCE_DIR}/.." "${_rg_lock}"
    RESULT_VARIABLE _rg_status
    OUTPUT_VARIABLE _rg_output
    ERROR_VARIABLE _rg_error
    TIMEOUT 15
)
if(NOT _rg_status EQUAL 0)
    message(FATAL_ERROR "Bundled ripgrep validation failed: ${_rg_error}")
endif()
install(PROGRAMS "${_rg_root}/bin/rg"
    DESTINATION runtime/ripgrep/x86_64-unknown-linux-musl/bin)
install(FILES "${_rg_lock}"
    DESTINATION runtime/ripgrep/x86_64-unknown-linux-musl RENAME MANIFEST.txt)
