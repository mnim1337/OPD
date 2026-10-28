# PyInstaller specification for the OPD desktop application.
# Build from the repository root with: pyinstaller --clean --noconfirm deploy/windows/opd-gui.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).resolve().parents[1]
hiddenimports = collect_submodules("scanner")

a = Analysis(
    [str(ROOT / "src/scanner/gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
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
