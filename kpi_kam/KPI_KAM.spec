# Shared onedir definition. Windows PE executable must be built on Windows.
from pathlib import Path

project = Path(SPECPATH)

analysis = Analysis(
    [str(project / 'desktop_entry.py')],
    pathex=[str(project)],
    binaries=[],
    datas=[],  # No input workbooks, test fixtures, user databases or logs.
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'tkinter', 'PySide6.QtTest'],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name='KPI_KAM',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
distribution = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name='KPI_KAM',
)
