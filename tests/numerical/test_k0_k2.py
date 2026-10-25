from __future__ import annotations

import copy
import json
import subprocess
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from zipfile import ZipFile

import pytest

from scanner.core import calculate
from scanner.core.scoring import expert_weights, normalize, resolve_profile, score
from scanner.core.prepare import prepare
from scanner.core.types import Value
from scanner.io.profile import validate_profile
from scanner.io.format import read_folder

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"
REFERENCE = ROOT / "requirements/PAK_export_reference_4files_v1.zip"
D = Decimal


def _close(actual: Decimal, expected: int | float | str, absolute: str = "0.000000001") -> bool:
    target = D(str(expected))
    return abs(actual - target) <= D(absolute) + D("0.000000001") * abs(target)


@pytest.fixture
def controls(tmp_path: Path):
    result = []
    with ZipFile(ARCHIVE) as archive:
        profile_path = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_path))
        folders: dict[str, list[str]] = {}
        for name in archive.namelist():
            if "/experiments/" in name and name.endswith((".json", ".csv")):
                folders.setdefault(str(Path(name).parent), []).append(name)
        for index, names in enumerate(sorted(folders.values())):
            folder = tmp_path / str(index)
            folder.mkdir()
            for name in names:
                (folder / Path(name).name).write_bytes(archive.read(name))
            experiment = read_folder(folder)
            expected_path = next(name for name in archive.namelist() if name.endswith(experiment.experiment_id + "_assessment.json"))
            oracle_path = next(name for name in archive.namelist() if name.endswith(experiment.experiment_id + "_oracle.json"))
            result.append((experiment, json.loads(archive.read(expected_path)), json.loads(archive.read(oracle_path))))
    return result, profile


def test_five_synthetic_controls_against_model_and_independent_oracle(controls) -> None:
    experiments, profile = controls
    assert len(experiments) == 5
    for experiment, expected, oracle in experiments:
        actual = calculate(experiment, profile)
        for key, result in actual.metrics.items():
            reference = expected["metrics"][key]
            oracle_value = oracle["expected_metrics"][key]["value"]
            if reference["value"] is None:
                assert result.raw.exact_value is None
                assert _close(result.raw.lower, reference["lower"])
                assert result.raw.upper is None
            else:
                assert result.raw.status == "exact"
                assert _close(result.raw.exact_value, reference["value"],
                              "0.000001" if key == "P_pr" else "0.000000001")
                assert _close(result.raw.exact_value, oracle_value,
                              "0.000001" if key == "P_pr" else "0.000000001")
            expected_score = expected["normalized_metrics"][key]["value"]
            assert _close(result.normalized.exact_value, expected_score)
            assert _close(result.contribution.exact_value,
                          expected["normalized_metrics"][key]["contribution_lower"], "0.0000001")
        for key, group in actual.groups.items():
            assert _close(group.exact_value, expected["group_scores"][key]["value"], "0.0000001")
        with localcontext() as context:
            context.prec = 50
            assert abs(sum((metric.contribution.exact_value for metric in actual.metrics.values()), D(0))
                       - actual.integral.exact_value) < D("1e-45")
        assert actual.integral.status == "exact"
        assert _close(actual.integral.exact_value, expected["integral_score"]["value"], "0.0000001")
        assert _close(actual.integral.exact_value, oracle["expected_integral_score"], "0.0000001")


def test_reference_hand_counts_and_late_reception(controls, tmp_path: Path) -> None:
    experiments, profile = controls
    result = calculate(experiments[0][0], profile)
    packet_trace = result.metrics["P_zad"].trace
    assert packet_trace["sent_packets"] == 480
    assert packet_trace["timely_received"] == 455
    assert packet_trace["received_in_window"] == 453
    assert packet_trace["received_after_window"] == 2
    assert result.metrics["P_pr"].raw.exact_value == D(453 * 1000 * 8) / D(12)
    assert abs(result.metrics["P_pot"].raw.exact_value - D(25) / D(480)) < D("1e-27")
    assert result.metrics["P_ad"].raw.exact_value == D("0.6")
    with ZipFile(REFERENCE) as archive:
        for name in archive.namelist():
            (tmp_path / name).write_bytes(archive.read(name))
    reference = calculate(read_folder(tmp_path), profile)
    assert reference.metrics["P_pr"].raw.exact_value == D(28800)
    assert reference.metrics["P_ad"].raw.exact_value == D("0.5")


def test_censored_adaptivity_has_open_lower_bound_but_saturated_score(controls) -> None:
    experiments, profile = controls
    result = calculate(experiments[4][0], profile)
    adaptive = result.metrics["P_ad"]
    assert adaptive.raw.status == "interval"
    assert adaptive.raw.lower == 2
    assert adaptive.raw.upper is None
    assert adaptive.raw.lower_open
    assert adaptive.normalized.exact_value == 0
    assert result.integral.status == "exact"
    changed = copy.deepcopy(profile)
    changed["metrics"]["P_ad"]["points"][-1][0] = 4
    with_longer_scale = calculate(experiments[4][0], changed)
    assert with_longer_scale.integral.status == "interval"
    assert with_longer_scale.integral.upper > with_longer_scale.integral.lower


def test_no_adaptivity_tasks_are_not_zero(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    experiment.passport["measurement"]["adaptivity"]["checks"] = []
    result = calculate(experiment, profile)
    assert result.metrics["P_ad"].raw.status == "not_applicable"
    assert result.integral.status == "not_applicable"


def test_multipath_record_does_not_invent_single_path_stability(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    experiment.passport["algorithm"]["routing_mode"] = "multipath_per_flow"
    result = calculate(experiment, profile)
    assert result.metrics["P_ust"].raw.status == "not_applicable"
    assert result.metrics["P_ad"].raw.status == "not_applicable"
    assert result.integral.status == "not_applicable"


def test_zero_traffic_bin_is_not_ideal_balance(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    first_start = min(row["time_start_s"] for row in experiment.tables["channels.csv"])
    for row in experiment.tables["channels.csv"]:
        if row["time_start_s"] == first_start:
            row["tx_data_bytes"] = 0
    result = calculate(experiment, profile)
    assert result.metrics["P_bal"].raw.status == "not_applicable"
    assert result.integral.status == "not_applicable"


def test_no_deliveries_make_delay_inapplicable_not_zero(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    for packet in experiment.tables["packets.csv"]:
        packet["rx_time_s"] = None
        packet["rx_copy_count"] = 0
        packet["drop_reason"] = "confirmed_drop"
    result = calculate(experiment, profile)
    assert result.metrics["P_zad"].raw.status == "not_applicable"
    assert result.metrics["P_pot"].raw.exact_value == 1
    assert result.metrics["P_pr"].raw.exact_value == 0
    assert result.integral.status == "not_applicable"


def test_extra_received_copy_does_not_add_useful_payload(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    baseline = calculate(experiment, profile)
    received = next(packet for packet in experiment.tables["packets.csv"] if packet["rx_time_s"] is not None)
    received["rx_copy_count"] += 1
    changed = calculate(experiment, profile)
    assert changed.metrics["P_pr"].raw == baseline.metrics["P_pr"].raw
    assert changed.metrics["P_zad"].raw == baseline.metrics["P_zad"].raw
    assert changed.metrics["P_pot"].raw == baseline.metrics["P_pot"].raw


def test_partial_packet_and_channel_counter_produce_bounds(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    experiment.tables["packets.csv"][0]["observation_status"] = "partial"
    row = experiment.tables["channels.csv"][0]
    row["observation_status"] = "partial"
    row["tx_routing_bytes"] = None
    result = calculate(experiment, profile)
    loss = result.metrics["P_pot"].raw
    service = result.metrics["P_res"].raw
    assert loss.status == "interval"
    assert abs((loss.upper - loss.lower) - D(1) / D(480)) < D("1e-27")
    assert service.status == "interval"
    assert service.upper > service.lower
    assert result.integral.status == "interval"


def test_uncertain_clock_does_not_yield_false_exact_times(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    experiment.passport["measurement"]["clock_error_bound_s"] = 0.02
    result = calculate(experiment, profile)
    for key in ("P_zad", "P_pr", "P_pot", "P_bal", "P_ust", "P_ad"):
        assert result.metrics[key].raw.status == "unknown"
    assert result.metrics["P_res"].raw.status == "exact"
    assert result.integral.status == "interval"


def test_expert_vectors_are_normalized_before_averaging(controls) -> None:
    _, profile = controls
    profile = copy.deepcopy(profile)
    first = {key: 1 for key in profile["metrics"]}
    second = {key: 2 for key in profile["metrics"]}
    first["P_zad"] = 7
    profile["experts"] = [{"scores": first}, {"scores": second}]
    for metric in profile["metrics"].values():
        metric.pop("weight")
    weights = expert_weights(profile["experts"])
    assert abs(weights["P_zad"] - (D(7) / D(13) + D(1) / D(7)) / 2) < D("1e-27")
    assert abs(sum(weights.values(), D(0)) - 1) < D("0.000000001")
    resolved = resolve_profile(profile)
    assert resolved["metrics"]["P_zad"]["weight"] == weights["P_zad"]


def test_scale_endpoints_saturation_and_zero_width(controls) -> None:
    _, profile = controls
    scale = profile["metrics"]["P_zad"]
    assert normalize(Value.exact("-1", "s"), scale).exact_value == 1
    assert normalize(Value.exact("100", "s"), scale).exact_value == 0
    invalid = copy.deepcopy(profile)
    invalid["metrics"]["P_zad"]["points"][1][0] = invalid["metrics"]["P_zad"]["points"][0][0]
    assert any(item.code == "PROFILE_ORDER" for item in validate_profile(invalid))


def test_unknown_existing_metric_keeps_all_original_weights(controls) -> None:
    experiments, profile = controls
    result = calculate(experiments[0][0], profile)
    raw = {key: item.raw for key, item in result.metrics.items()}
    raw["P_res"] = Value.unknown("byte", "неполный учёт")
    _, _, integral = score(raw, {key: item.trace for key, item in result.metrics.items()}, profile)
    assert integral.status == "interval"
    assert integral.upper - integral.lower == 8


def test_k0_keeps_old_installed_route_after_physical_failure(controls) -> None:
    experiments, _ = controls
    context = prepare(experiments[0][0])
    event = context.events["E01"]
    instant = D(str(event["actual_time_s"]))
    old = context.route_at("F01", instant, before=True)
    still_recorded = context.route_at("F01", instant)
    assert old["path_links"] == still_recorded["path_links"]
    assert context.physical_at(old["path_links"], instant, before=True) is True
    assert context.physical_at(old["path_links"], instant) is False
    assert context.connected_at("F01", instant) is True


def test_deadline_at_observation_boundary_is_unknown_not_confirmed_loss(controls) -> None:
    experiments, profile = controls
    experiment = copy.deepcopy(experiments[0][0])
    packet = next(row for row in experiment.tables["packets.csv"] if row["rx_time_s"] is None)
    packet["drop_reason"] = ""
    packet["observed_until_s"] = packet["tx_time_s"] + D(str(experiment.passport["measurement"]["loss_timeout_s"]))
    result = calculate(experiment, profile)
    loss = result.metrics["P_pot"].raw
    assert loss.status == "interval"
    assert abs((loss.upper - loss.lower) - D(1) / D(480)) < D("1e-27")


def test_headless_cli_calculation_writes_finite_json(controls, tmp_path: Path) -> None:
    experiments, profile = controls
    source = tmp_path / "source"
    source.mkdir()
    from scanner.io.format import export_four
    export_four(experiments[0][0], source)
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "assessment.json"
    run = subprocess.run([sys.executable, "-m", "scanner.cli", "calculate", str(source),
                          "--profile", str(profile_path), "--output", str(output)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["experiment_id"] == "EXP01_REACTIVE_4"
    assert payload["integral"]["status"] == "exact"
    assert "Infinity" not in output.read_text(encoding="utf-8")
