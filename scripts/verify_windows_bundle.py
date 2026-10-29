"""Fail the Windows build if its GUI archive lacks the required Qt plugins."""

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader


REQUIRED = {
    "pyside6/plugins/platforms/qwindows.dll",
    "pyside6/plugins/platforms/qoffscreen.dll",
    "pyside6/qt6core.dll",
    "pyside6/qt6gui.dll",
    "pyside6/qt6widgets.dll",
}


def main(path: Path) -> int:
    if not path.is_file():
        raise SystemExit(f"GUI executable is missing: {path}")
    entries = {name.replace("\\", "/").lower() for name in CArchiveReader(str(path)).toc}
    missing = sorted(REQUIRED - entries)
    if missing:
        raise SystemExit("Windows GUI bundle is missing: " + ", ".join(missing))
    print("Windows GUI bundle contains Qt libraries, qwindows.dll and qoffscreen.dll")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
