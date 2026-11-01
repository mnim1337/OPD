from __future__ import annotations

import copy
import json
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

from scanner.core import calculate
from scanner.core.sensitivity import analyze_sensitivity, analyze_saved_sensitivity, max_scale_slope
from scanner.io.format import read_folder

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def comparable_pair(reference_folder: Path):
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    second.passport["experiment_id"] = "REF_ROUTING_002"
    for rows in second.tables.values():
        for row in rows:
            row["experiment_id"] = "REF_ROUTING_002"
    second.tables["channels.csv"][0]["tx_routing_bytes"] += 100
    profile = json.loads((ROOT / "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    return first, second, profile


def test_sensitivity_bound_and_sufficient_preference(comparable_pair) -> None:
    first, second, profile = comparable_pair
    report = analyze_sensitivity(first, second, profile, "0.001")
    delta = D(report["score_difference_points_3_51"])
    physical = D(report["physical_change_bound_points_3_48"])
    assert delta > 0
    assert abs(delta) <= physical
    assert D(report["weight_change_bound_points_3_49"]) == D("0.05")
    assert D(report["joint_change_bound_points_3_50"]) == physical + D("0.05")
    assert D(report["difference_change_bound_points_3_52"]) == D("0.1")
    assert report["conclusion"] == ("first_remains_higher" if delta > D("0.1") else "inconclusive")
    assert analyze_sensitivity(first, second, profile, 2)["conclusion"] == "inconclusive"


def test_slope_uses_largest_segment_and_saturation(comparable_pair) -> None:
    assert max_scale_slope([[0, 1], [10, "0.8"], [20, 0]]) == D("0.08")
    first, _, profile = comparable_pair
    same = analyze_sensitivity(first, first, profile)
    assert D(same["physical_change_bound_points_3_48"]) == 0
    assert D(same["score_difference_points_3_51"]) == 0
    assert same["conclusion"] == "inconclusive"


def test_sensitivity_rejects_incomparable_or_inexact_inputs(comparable_pair) -> None:
    first, second, profile = comparable_pair
    second.passport["traffic"]["flows"][0]["packet_size_bytes"] = 999
    with pytest.raises(ValueError, match="несопоставимы"):
        analyze_sensitivity(first, second, profile)
    second.passport["traffic"] = copy.deepcopy(first.passport["traffic"])
    second.passport["measurement"]["clock_error_bound_s"] = 0.02
    with pytest.raises(ValueError, match="несопоставимы"):
        analyze_sensitivity(first, second, profile)
    first.passport["measurement"]["clock_error_bound_s"] = 0.02
    with pytest.raises(ValueError, match="точного значения: итог"):
        analyze_sensitivity(first, second, profile)
    with pytest.raises(ValueError, match="от 0 до 2"):
        analyze_sensitivity(first, second, profile, -1)


def test_saved_sensitivity_uses_persisted_values_without_recalculation(comparable_pair, monkeypatch) -> None:
    first, second, profile = comparable_pair
    left = calculate(first, profile).as_dict()
    right = calculate(second, profile).as_dict()
    monkeypatch.setattr("scanner.core.sensitivity.calculate", lambda *_a, **_k: pytest.fail("recalculated"))
    report = analyze_saved_sensitivity(first, left, profile, second, right, profile, "0.01")
    assert D(report["score_difference_points_3_51"]) > 0
    right["model_version"] = "other-model"
    with pytest.raises(ValueError, match="версии расчётной модели"):
        analyze_saved_sensitivity(first, left, profile, second, right, profile)


def test_cli_sensitivity_writes_json(reference_folder: Path, tmp_path: Path) -> None:
    output = tmp_path / "sensitivity.json"
    result = subprocess.run(
        [sys.executable, "-m", "scanner.cli", "sensitivity", str(reference_folder),
         str(reference_folder), "--profile", str(ROOT / "configs/examples/profile_demo.json"),
         "--epsilon-weight", "0.1", "--output", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert "Анализ чувствительности" in result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["conclusion"] == "inconclusive"
    assert D(payload["score_difference_points_3_51"]) == 0
