from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

from scanner.io.profile import validate_profile

ROOT = Path(__file__).resolve().parents[2]
FIVE = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"


def test_demo_profile_and_invalid_weight() -> None:
    with ZipFile(FIVE) as archive:
        name = next(name for name in archive.namelist() if name.endswith("profile_demo.json"))
        profile = json.loads(archive.read(name))
    assert validate_profile(profile) == []
    profile["metrics"]["P_zad"]["weight"] = 0
    codes = {item.code for item in validate_profile(profile)}
    assert "PROFILE_WEIGHT" in codes
    assert "PROFILE_SUM" in codes
