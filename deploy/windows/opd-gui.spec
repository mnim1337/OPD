# PyInstaller specification for the OPD desktop application.
# Build from the repository root with: pyinstaller --clean --noconfirm deploy/windows/opd-gui.spec

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules, get_package_paths


ROOT = Path(SPECPATH).resolve().parents[1]
hiddenimports = collect_submodules("scanner")
qt_platform_binaries = []
if sys.platform == "win32":
    # PyInstaller 6.11.1 with PySide6 Essentials 6.12.0 may omit platform plugins.
    # The PySide6 runtime hook searches <bundle>/PySide6/plugins on Windows.
    _base, pyside_dir = get_package_paths("PySide6")
    platform_dir = Path(pyside_dir) / "plugins/platforms"
    for name in ("qwindows.dll", "qoffscreen.dll"):
        source = platform_dir / name
        if not source.is_file():
            raise FileNotFoundError(f"Required Qt platform plugin is missing: {source}")
        qt_platform_binaries.append((str(source), "PySide6/plugins/platforms"))

a = Analysis(
    [str(ROOT / "deploy/windows/gui_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=qt_platform_binaries,
    datas=[(str(ROOT / "configs/examples/profile_demo.json"), "configs/examples")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="opd-gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
