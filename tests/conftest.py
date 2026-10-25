from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "requirements/PAK_export_reference_4files_v1.zip"
FIVE = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_20_files.zip"


@pytest.fixture
def reference_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "reference"
    folder.mkdir()
    with ZipFile(REFERENCE) as archive:
        for name in archive.namelist():
            (folder / name).write_bytes(archive.read(name))
    return folder
