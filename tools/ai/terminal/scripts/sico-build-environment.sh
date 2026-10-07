# Retired build settings are rejected or cleared; only SICO inputs supply values.
# Values are data; only this fixed list determines eval variable names.
for sico_suffix in AI_BUILD_JOBS AI_CMAKE_TOOLCHAIN_FILE AI_LXQT_BUILD_TOOLS_REPO AI_PYQT5_BINDINGS_DIR AI_PYQT5_PYTHON AI_PYQT5_QMAKE AI_PYQT5_SIP_DIR AI_PYQT_CC AI_PYQT_CFLAGS AI_PYQT_COMPILER_LIBRARY_PATH AI_PYQT_CXX AI_PYQT_CXXFLAGS AI_PYQT_LFLAGS AI_PYQT_PYTHON AI_PYQT_PYTHON_INCLUDE_DIR AI_PYQT_QMAKE AI_PYQT_QT_ROOT AI_PYTHON_LIBRARY_PATH AI_QT5_DIR AI_QT5_ROOT AI_QTERMWIDGET_REPO AI_REFERENCE_VALIDATION_LD_LIBRARY_PATH AI_REFERENCE_VALIDATION_PYTHON AI_RHEL7_GCC_ROOT AI_RHEL7_LIBGCRYPT_RPM AI_RHEL7_LIBGPG_ERROR_RPM AI_RHEL7_LIBX11_DEVEL_RPM AI_RHEL7_QTBASE_GUI_RPM AI_RHEL7_XORG_X11_PROTO_DEVEL_RPM AI_SMOKE_EXTENSION_DIR AI_SMOKE_RUNTIME_ROOT AI_SYSTEM_LD_LIBRARY_PATH CODEX_SYSTEM_LD_LIBRARY_PATH PYTHON PYTHON_ROOT SYSTEM_LD_LIBRARY_PATH; do
    eval 'sico_current_set=${SICO_'"$sico_suffix"'+x}'
    eval 'sico_current_value=${SICO_'"$sico_suffix"'-}'
    eval 'sico_legacy_set=${CAD_'"$sico_suffix"'+x}'
    if [ "$sico_current_set" = x ]; then
        if [ "$sico_legacy_set" = x ]; then
            echo "CAD_$sico_suffix conflicts with SICO_$sico_suffix; SICO_$sico_suffix takes precedence" >&2
        fi
    elif [ "$sico_legacy_set" = x ]; then
        echo "CAD_$sico_suffix was removed after SiCo v0.0.1; set SICO_$sico_suffix" >&2
        return 2
    fi
    unset "CAD_$sico_suffix"
done
unset sico_suffix sico_current_set sico_current_value sico_legacy_set sico_legacy_value
