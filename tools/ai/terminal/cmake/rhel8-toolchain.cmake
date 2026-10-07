include("${CMAKE_CURRENT_LIST_DIR}/sico-options.cmake")

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

set(_cad_ai_default_gcc_root "/software/pkgs/gcc/v8.5.0")
if(DEFINED ENV{SICO_AI_RHEL8_GCC_ROOT})
    set(_cad_ai_default_gcc_root "$ENV{SICO_AI_RHEL8_GCC_ROOT}")
endif()
set(_cad_ai_default_qt_root "/software/pkgs/qt/5.15.3-rhel8")
if(DEFINED ENV{SICO_AI_RHEL8_QT_ROOT})
    set(_cad_ai_default_qt_root "$ENV{SICO_AI_RHEL8_QT_ROOT}")
elseif(DEFINED ENV{SICO_AI_QT5_ROOT})
    set(_cad_ai_default_qt_root "$ENV{SICO_AI_QT5_ROOT}")
endif()

set(SICO_AI_RHEL8_GCC_ROOT "${_cad_ai_default_gcc_root}" CACHE PATH
    "RHEL 8 GCC headers, libraries, and glibc sysroot")
set(SICO_AI_RHEL8_QT_ROOT "${_cad_ai_default_qt_root}" CACHE PATH
    "Relocated RHEL 8 Qt 5.15 installation")
unset(_cad_ai_default_gcc_root)
unset(_cad_ai_default_qt_root)

set(_cad_ai_sysroot "${SICO_AI_RHEL8_GCC_ROOT}/sysroot")
set(_cad_ai_gcc_include "${SICO_AI_RHEL8_GCC_ROOT}/usr/include/c++/8")
set(_cad_ai_gcc_target_include
    "${_cad_ai_gcc_include}/x86_64-redhat-linux")
set(_cad_ai_gcc_lib "${SICO_AI_RHEL8_GCC_ROOT}/usr/lib64")
set(_cad_ai_qt_lib "${SICO_AI_RHEL8_QT_ROOT}/usr/lib64")

foreach(_required_path
        "${_cad_ai_sysroot}/usr/include"
        "${_cad_ai_gcc_include}"
        "${_cad_ai_gcc_target_include}"
        "${_cad_ai_gcc_lib}/libstdc++.so"
        "${_cad_ai_qt_lib}/libQt5Core.so")
    if(NOT EXISTS "${_required_path}")
        message(FATAL_ERROR "RHEL 8 toolchain path is missing: ${_required_path}")
    endif()
endforeach()

# Use the host GCC driver with the EL8 sysroot and GCC 8 C++ runtime. The
# extracted GCC 8 compiler binaries are not usable as CMake compiler drivers.
set(CMAKE_C_COMPILER "/usr/bin/gcc" CACHE FILEPATH "" FORCE)
set(CMAKE_CXX_COMPILER "/usr/bin/g++" CACHE FILEPATH "" FORCE)
set(CMAKE_SYSROOT "${_cad_ai_sysroot}" CACHE PATH "" FORCE)

set(CMAKE_C_FLAGS_INIT
    "-march=x86-64 -B${_cad_ai_sysroot}/usr/lib64")
string(CONCAT CMAKE_CXX_FLAGS_INIT
    "-march=x86-64 -B${_cad_ai_sysroot}/usr/lib64"
    " -nostdinc++"
    " -isystem ${_cad_ai_gcc_include}"
    " -isystem ${_cad_ai_gcc_target_include}"
    " -isystem ${_cad_ai_gcc_include}/backward")

string(CONCAT _cad_ai_link_flags
    "-L${_cad_ai_gcc_lib} -L${_cad_ai_qt_lib}"
    " -Wl,--hash-style=gnu"
    " -Wl,-rpath-link,${_cad_ai_qt_lib}"
    " -Wl,-rpath-link,${_cad_ai_sysroot}/usr/lib64"
    " -Wl,-rpath-link,${_cad_ai_sysroot}/lib64"
    " -Wl,--allow-shlib-undefined")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH FALSE)

list(PREPEND CMAKE_FIND_ROOT_PATH
    "${_cad_ai_sysroot}"
    "${SICO_AI_RHEL8_QT_ROOT}"
    "${SICO_AI_RHEL8_GCC_ROOT}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)
set(CMAKE_FIND_PACKAGE_PREFER_CONFIG ON)
