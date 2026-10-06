@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
if /I not "%OS%"=="Windows_NT" (
    echo This build requires Windows with 64-bit Python 3.12.
    exit /b 2
)
pushd "%~dp0"
if errorlevel 1 exit /b 2
set "KPI_BUILD_VENV=%CD%\.venv-build"
if not exist "%KPI_BUILD_VENV%\Scripts\python.exe" (
    if defined KPI_BUILD_PYTHON (
        "%KPI_BUILD_PYTHON%" -m venv "%KPI_BUILD_VENV%"
    ) else (
        py -3.12 -m venv "%KPI_BUILD_VENV%"
    )
    if errorlevel 1 goto :failed
)
set "KPI_BUILD_PYTHON_BIN=%KPI_BUILD_VENV%\Scripts\python.exe"
"%KPI_BUILD_PYTHON_BIN%" -c "import sys; assert sys.platform == 'win32' and sys.version_info[:2] == (3,12) and sys.maxsize > 2**32, 'Use 64-bit Windows Python 3.12'"
if errorlevel 1 goto :failed
"%KPI_BUILD_PYTHON_BIN%" -m pip install -r requirements-build.txt
if errorlevel 1 goto :failed
set "QT_QPA_PLATFORM=offscreen"
set "XDG_CACHE_HOME=%TEMP%\kpi-kam-build-cache"
"%KPI_BUILD_PYTHON_BIN%" -m pytest -q
if errorlevel 1 goto :failed
"%KPI_BUILD_PYTHON_BIN%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller KPI_KAM.spec
if errorlevel 1 goto :failed
if not exist "dist\KPI_KAM\KPI_KAM.exe" (
    echo PyInstaller did not produce dist\KPI_KAM\KPI_KAM.exe.
    goto :failed
)
"%KPI_BUILD_PYTHON_BIN%" tools\smoke_frozen.py --executable dist\KPI_KAM\KPI_KAM.exe --fixture "tests\fixtures\KPI Трофимов Дмитрий 2026.xlsm" --expected tests\fixtures\trofimov_expected.json --report build\packaging-smoke.json
if errorlevel 1 goto :failed
echo Build and frozen verification passed: dist\KPI_KAM\KPI_KAM.exe
popd
exit /b 0

:failed
set "KPI_BUILD_EXIT=%ERRORLEVEL%"
if "%KPI_BUILD_EXIT%"=="0" set "KPI_BUILD_EXIT=1"
echo Windows build failed. See the command output above.
popd
exit /b %KPI_BUILD_EXIT%
