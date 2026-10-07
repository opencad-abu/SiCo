include("${CMAKE_CURRENT_LIST_DIR}/sico-options.cmake")

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

set(_cad_ai_default_gcc_root "/software/pkgs/gcc/v4.8.5-rhel7")
if(DEFINED ENV{SICO_AI_RHEL7_GCC_ROOT})
    set(_cad_ai_default_gcc_root "$ENV{SICO_AI_RHEL7_GCC_ROOT}")
endif()
set(_cad_ai_default_qt_root "/software/pkgs/qt/5.9.7-rhel7")
if(DEFINED ENV{SICO_AI_RHEL7_QT_ROOT})
    set(_cad_ai_default_qt_root "$ENV{SICO_AI_RHEL7_QT_ROOT}")
elseif(DEFINED ENV{SICO_AI_QT5_ROOT})
    set(_cad_ai_default_qt_root "$ENV{SICO_AI_QT5_ROOT}")
endif()
set(_cad_ai_default_deps_root "/software/pkgs/rhel7-deps")
if(DEFINED ENV{SICO_AI_RHEL7_DEPS_ROOT})
    set(_cad_ai_default_deps_root "$ENV{SICO_AI_RHEL7_DEPS_ROOT}")
endif()

set(SICO_AI_RHEL7_GCC_ROOT "${_cad_ai_default_gcc_root}" CACHE PATH
    "RHEL 7 GCC 4.8.5 headers, libraries, and glibc 2.17 sysroot")
set(SICO_AI_RHEL7_QT_ROOT "${_cad_ai_default_qt_root}" CACHE PATH
    "Relocated RHEL 7 Qt 5.9 installation")
set(SICO_AI_RHEL7_DEPS_ROOT "${_cad_ai_default_deps_root}" CACHE PATH
    "RHEL 7 dependency prefix used by the Qt build")
unset(_cad_ai_default_gcc_root)
unset(_cad_ai_default_qt_root)
unset(_cad_ai_default_deps_root)

set(_cad_ai_sysroot "${SICO_AI_RHEL7_GCC_ROOT}/sysroot")
set(_cad_ai_gcc_include "${SICO_AI_RHEL7_GCC_ROOT}/usr/include/c++/4.8.2")
set(_cad_ai_gcc_target_include
    "${_cad_ai_gcc_include}/x86_64-redhat-linux")
set(_cad_ai_gcc_lib "${SICO_AI_RHEL7_GCC_ROOT}/usr/lib64")
set(_cad_ai_qt_lib "${SICO_AI_RHEL7_QT_ROOT}/usr/lib64")
set(_cad_ai_deps_lib "${SICO_AI_RHEL7_DEPS_ROOT}/usr/lib64")
set(ENV{SICO_AI_RHEL7_GCC_ROOT} "${SICO_AI_RHEL7_GCC_ROOT}")

foreach(_required_path
        "${_cad_ai_sysroot}/usr/include"
        "${_cad_ai_gcc_include}"
        "${_cad_ai_gcc_target_include}"
        "${_cad_ai_gcc_lib}/libstdc++.so"
        "${_cad_ai_qt_lib}/libQt5Core.so")
    if(NOT EXISTS "${_required_path}")
        message(FATAL_ERROR "RHEL 7 toolchain path is missing: ${_required_path}")
    endif()
endforeach()

set(CMAKE_C_COMPILER "${CMAKE_CURRENT_LIST_DIR}/rhel7-gcc.sh" CACHE FILEPATH "" FORCE)
set(CMAKE_CXX_COMPILER "${CMAKE_CURRENT_LIST_DIR}/rhel7-g++.sh" CACHE FILEPATH "" FORCE)
set(CMAKE_SYSROOT "${_cad_ai_sysroot}" CACHE PATH "" FORCE)

set(CMAKE_C_FLAGS_INIT "-march=x86-64 -B${_cad_ai_sysroot}/usr/lib64")
string(CONCAT CMAKE_CXX_FLAGS_INIT
    "-march=x86-64 -B${_cad_ai_sysroot}/usr/lib64"
    " -nostdinc++"
    " -isystem ${_cad_ai_gcc_include}"
    " -isystem ${_cad_ai_gcc_target_include}"
    " -isystem ${_cad_ai_gcc_include}/backward")

string(CONCAT _cad_ai_link_flags
    "-L${_cad_ai_gcc_lib} -L${_cad_ai_qt_lib} -L${_cad_ai_deps_lib}"
    " -Wl,--hash-style=gnu"
    " -Wl,-rpath-link,${_cad_ai_qt_lib}"
    " -Wl,-rpath-link,${_cad_ai_deps_lib}"
    " -Wl,-rpath-link,${_cad_ai_sysroot}/usr/lib64"
    " -Wl,-rpath-link,${_cad_ai_sysroot}/lib64"
    " -Wl,--allow-shlib-undefined")
set(CMAKE_EXE_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "${_cad_ai_link_flags}")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH FALSE)

list(PREPEND CMAKE_FIND_ROOT_PATH
    "${_cad_ai_sysroot}"
    "${SICO_AI_RHEL7_QT_ROOT}"
    "${SICO_AI_RHEL7_GCC_ROOT}"
    "${SICO_AI_RHEL7_DEPS_ROOT}")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE BOTH)
set(CMAKE_FIND_PACKAGE_PREFER_CONFIG ON)

# Qt 5.9.7 on RHEL 7 links against ICU 50. The platform plugin tree is built
# separately because the installed Qt SDK intentionally leaves it empty.
set(SICO_AI_ICU_RUNTIME_SONAMES
    "libicudata.so.50;libicuuc.so.50;libicui18n.so.50" CACHE STRING "" FORCE)
set(SICO_AI_QT5_RUNTIME_PLUGINS
    "platforms/libqxcb.so;platforms/libqoffscreen.so;platforminputcontexts/libcomposeplatforminputcontextplugin.so;platforminputcontexts/libibusplatforminputcontextplugin.so"
    CACHE STRING "" FORCE)
set(SICO_AI_QT5_LICENSE_DIR
    "${SICO_AI_RHEL7_QT_ROOT}/usr/share/licenses/qt5-qtbase-5.9.7"
    CACHE PATH "" FORCE)
set(SICO_AI_QT5_X11EXTRAS_LICENSE_DIR
    "${SICO_AI_RHEL7_QT_ROOT}/usr/share/licenses/qt5-qtx11extras-5.9.7"
    CACHE PATH "" FORCE)
set(SICO_AI_ICU_LICENSE_DIR
    "${SICO_AI_RHEL7_QT_ROOT}/usr/share/doc/libicu-50.2"
    CACHE PATH "" FORCE)
