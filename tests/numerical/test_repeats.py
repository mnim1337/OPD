from __future__ import annotations

import copy
import json
from decimal import Decimal as D, localcontext
from pathlib import Path
from zipfile import ZipFile

import pytest

from scanner.core import calculate
from scanner.core.repeats import analyze_repeats, analyze_saved_repeats
from scanner.io.format import read_folder

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def runs(reference_folder: Path):
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    first.passport["repeat_id"] = 1
    second.passport["repeat_id"] = 2
    second.passport["experiment_id"] = "REF_ROUTING_002"
    for rows in second.tables.values():
        for row in rows:
            row["experiment_id"] = "REF_ROUTING_002"
    second.tables["channels.csv"][0]["tx_routing_bytes"] += 100
    profile = json.loads((ROOT / "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    return first, second, profile


def test_repeats_aggregate_integral_after_each_calculation(runs) -> None:
    first, second, profile = runs
    report = analyze_repeats([first, second], profile)
    a = calculate(first, profile).integral.exact_value
    b = calculate(second, profile).integral.exact_value
    assert [D(row["integral_points"]) for row in report["runs"]] == [a, b]
    with localcontext() as context:
        context.prec = 50
        assert D(report["mean_points_3_53"]) == (a + b) / 2
        assert abs(D(report["sample_variance_points_squared_3_55"]) - (a - b) ** 2 / 2) < D("1e-45")
    assert report["standard_error_points_3_55"] is None
    assert report["student_interval_3_56"] is None
    with_basis = analyze_repeats([first, second], profile, independence_basis="Отдельные случайные запуски, протокол X")
    assert with_basis["standard_error_points_3_55"] is not None
    assert with_basis["student_interval_3_56"] is None


def test_repeats_reject_different_scenarios_and_cloned_ids(runs) -> None:
    first, second, profile = runs
    with pytest.raises(ValueError, match="разные experiment_id"):
        analyze_repeats([first, first], profile)
    second.passport["repeat_id"] = 1
    with pytest.raises(ValueError, match="разные заполненные repeat_id"):
        analyze_repeats([first, second], profile)
    second.passport["repeat_id"] = 2
    second.passport["scenario_id"] = "OTHER"
    with pytest.raises(ValueError, match="scenario_id"):
        analyze_repeats([first, second], profile)
    second.passport["scenario_id"] = first.passport["scenario_id"]
    second.passport["algorithm"]["algorithm_id"] = "OTHER"
    with pytest.raises(ValueError, match="алгоритмы"):
        analyze_repeats([first, second], profile)


def test_single_run_has_no_variance_or_standard_error(runs) -> None:
    first, _, profile = runs
    report = analyze_repeats([first], profile, independence_basis="один опыт")
    assert report["count"] == 1
    assert report["sample_variance_points_squared_3_55"] is None
    assert report["standard_error_points_3_55"] is None


def test_five_control_scenarios_are_not_statistical_repeats(tmp_path: Path) -> None:
    archive_path = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"
    experiments = []
    with ZipFile(archive_path) as archive:
        folders = sorted({str(Path(name).parent) for name in archive.namelist()
                          if "/experiments/" in name and name.endswith("experiment.json")})
        for index, folder_name in enumerate(folders):
            folder = tmp_path / str(index)
            folder.mkdir()
            for name in archive.namelist():
                if str(Path(name).parent) == folder_name and Path(name).name in {
                    "experiment.json", "packets.csv", "routes.csv", "channels.csv"
                }:
                    (folder / Path(name).name).write_bytes(archive.read(name))
            experiment = read_folder(folder)
            experiment.passport["repeat_id"] = index + 1  # Remove the duplicate-ID blocker to test the scenario guard.
            experiments.append(experiment)
    assert len(experiments) == 5
    profile = json.loads((ROOT / "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="scenario_id|алгоритмы|Несопоставимые"):
        analyze_repeats(experiments, profile)


def test_saved_repeats_use_recorded_integrals_and_reject_mixed_models(runs, monkeypatch) -> None:
    first, second, profile = runs
    records = [(item, calculate(item, profile).as_dict(), profile) for item in (first, second)]
    monkeypatch.setattr("scanner.core.repeats.calculate", lambda *_a, **_k: pytest.fail("recalculated"))
    report = analyze_saved_repeats(records)
    assert report["count"] == 2
    assert report["student_interval_3_56"] is None
    assert report["standard_error_points_3_55"] is None
    records[1][1]["model_version"] = "other"
    with pytest.raises(ValueError, match="версию расчётной модели"):
        analyze_saved_repeats(records)
