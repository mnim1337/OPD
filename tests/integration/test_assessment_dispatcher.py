from __future__ import annotations

import json
import shutil
import copy
from threading import Event
from pathlib import Path
from zipfile import ZipFile

import pytest

from scanner.core import dispatcher
from scanner.core.comparison import comparability_reasons
from scanner.core.dispatcher import ResultPersistenceError, calculate_and_save
from scanner.core.runner import CalculationCancelled
from scanner.io.format import read_folder
from scanner.io.storage import DraftStore


FIVE_EXPERIMENTS = Path(__file__).resolve().parents[2] / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"


def test_dispatcher_pins_input_and_profile_and_history_loads_without_recalculation(
    tmp_path: Path,
) -> None:
    with ZipFile(FIVE_EXPERIMENTS) as archive:
        input_name = next(name for name in archive.namelist() if name.endswith("/experiment.json"))
        prefix = input_name.rsplit("/", 1)[0] + "/"
        folder = tmp_path / "input"
        folder.mkdir()
        for name in archive.namelist():
            if name.startswith(prefix):
                (folder / name.removeprefix(prefix)).write_bytes(archive.read(name))
        profile_name = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))

    store = DraftStore(tmp_path / "drafts.sqlite3")
    experiment = read_folder(folder)
    assessment_id, revision, calculated = calculate_and_save(store, "saved-demo", experiment, profile)
    shutil.rmtree(folder)
    listed = store.list_assessments()
    assert len(listed) == 1
    assert listed[0][:4] == (assessment_id, "saved-demo", revision, calculated["model_version"])
    saved, saved_experiment, saved_profile = store.load_assessment(assessment_id)
    assert saved == calculated
    assert saved_experiment.experiment_id == experiment.experiment_id
    assert saved_experiment.tables == experiment.tables
    assert saved_profile == profile
    series_id = store.create_series("Контрольная серия")
    assert store.add_to_series(series_id, assessment_id) == 1
    assert store.add_to_series(series_id, assessment_id) == 1
    assert store.series_assessments(series_id)[0][0] == assessment_id
    backup_path = tmp_path / "saved-assessment-backup.sqlite3"
    store.backup(backup_path)
    assert DraftStore(backup_path).load_assessment(assessment_id)[0] == calculated
    restored = DraftStore(tmp_path / "restored.sqlite3")
    restored.restore(backup_path)
    assert restored.load_assessment(assessment_id)[0] == calculated
    assert restored.list_series()[0][:3] == (series_id, "Контрольная серия", "open")
    store.close_series(series_id)
    with pytest.raises(ValueError):
        store.add_to_series(series_id, assessment_id)

    def unexpected_recalculation(*_args, **_kwargs):
        raise AssertionError("Загрузка истории не должна запускать расчёт")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dispatcher, "calculate", unexpected_recalculation)
    try:
        loaded, _, _ = store.load_assessment(assessment_id)
        assert loaded == calculated
    finally:
        monkeypatch.undo()


def test_failed_result_write_can_be_retried_without_recalculation(tmp_path: Path, monkeypatch) -> None:
    with ZipFile(FIVE_EXPERIMENTS) as archive:
        input_name = next(name for name in archive.namelist() if name.endswith("/experiment.json"))
        prefix = input_name.rsplit("/", 1)[0] + "/"
        folder = tmp_path / "input"
        folder.mkdir()
        for name in archive.namelist():
            if name.startswith(prefix):
                (folder / name.removeprefix(prefix)).write_bytes(archive.read(name))
        profile_name = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))

    store = DraftStore(tmp_path / "drafts.sqlite3")
    original_save = store.save_assessment
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("simulated storage outage")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(store, "save_assessment", fail_once)
    with pytest.raises(ResultPersistenceError) as caught:
        calculate_and_save(store, "retry-demo", read_folder(folder), profile)
    pending = caught.value

    # Retry only the already calculated payload; calculation is deliberately not called.
    monkeypatch.setattr(dispatcher, "calculate", lambda *_args, **_kwargs: pytest.fail("recalculated"))
    result_id = store.save_assessment(
        pending.draft_id, pending.revision, pending.profile_id,
        pending.profile_version, pending.model_version, pending.payload,
        pending.assessment_id,
    )
    assert result_id
    assert len(store.list_assessments()) == 1


def test_sqlite_write_abort_preserves_old_assessment_and_retry_payload(
    reference_folder: Path, tmp_path: Path,
) -> None:
    profile = json.loads((Path(__file__).resolve().parents[2] / "configs/examples/profile_demo.json").read_text())
    experiment = read_folder(reference_folder)
    store = DraftStore(tmp_path / "drafts.sqlite3")
    old_id, old_revision, old_payload = calculate_and_save(store, "failure-check", experiment, profile)
    with store._connect() as db:
        db.execute("""CREATE TRIGGER reject_new_assessment BEFORE INSERT ON assessments
                      BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
    with pytest.raises(ResultPersistenceError) as caught:
        calculate_and_save(store, "failure-check", experiment, profile)
    pending = caught.value
    assert pending.revision == old_revision + 1
    assert pending.payload["integral"] == old_payload["integral"]
    assert [row[0] for row in store.list_assessments()] == [old_id]
    assert store.load_assessment(old_id)[0] == old_payload
    with store._connect() as db:
        db.execute("DROP TRIGGER reject_new_assessment")
    saved_id = store.save_assessment(
        pending.draft_id, pending.revision, pending.profile_id, pending.profile_version,
        pending.model_version, pending.payload, pending.assessment_id,
    )
    assert saved_id == pending.assessment_id
    assert len(store.list_assessments()) == 2
    assert store.load_assessment(old_id)[0] == old_payload


def test_backup_is_consistent_and_cannot_overwrite_active_store(tmp_path: Path) -> None:
    store = DraftStore(tmp_path / "drafts.sqlite3")
    with pytest.raises(ValueError):
        store.backup(store.path)
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    assert backup.exists()
    assert DraftStore(backup).list_assessments() == []
    with pytest.raises(ValueError):
        store.restore(store.path)


def test_comparability_ignores_measured_outcomes_but_checks_conditions(
    reference_folder: Path,
) -> None:
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    second.tables["routes.csv"][0]["route_state"] = "unknown"
    second.tables["channels.csv"][0]["tx_data_bytes"] = 0
    if second.passport.get("scenario", {}).get("events"):
        second.passport["scenario"]["events"][0]["actual_time_s"] = 999
        second.passport["scenario"]["events"][0]["result"] = "failed"
    profile = {"metrics": {}}
    assert comparability_reasons(first, profile, second, profile) == []

    second.passport["network"]["network_id"] = "different-network"
    assert comparability_reasons(first, profile, second, profile) == ["Состав или исходное состояние сети"]
    second.passport["network"]["network_id"] = first.passport["network"]["network_id"]
    if second.passport.get("scenario", {}).get("events"):
        second.passport["scenario"]["events"][0]["scheduled_time_s"] += 1
        assert comparability_reasons(first, profile, second, profile) == ["Внешние события и их расписание"]


def test_comparability_checks_execution_conditions_but_allows_different_random_seeds(
    reference_folder: Path,
) -> None:
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    profile = json.loads((Path(__file__).resolve().parents[2] / "configs/examples/profile_demo.json").read_text())
    first.passport["environment"]["randomness"] = {"used": True, "seeds": {"traffic": 1}}
    second.passport["environment"]["randomness"] = {"used": True, "seeds": {"traffic": 2}}
    assert comparability_reasons(first, profile, second, profile) == []

    second.passport["environment"]["queue_discipline"] = "different"
    assert comparability_reasons(first, profile, second, profile) == [
        "Условия выполнения, очереди, буферы или модель ошибок"]
    second.passport["environment"]["queue_discipline"] = first.passport["environment"]["queue_discipline"]
    second.passport["environment"]["link_errors"] = {"model": "random_loss"}
    assert comparability_reasons(first, profile, second, profile) == [
        "Условия выполнения, очереди, буферы или модель ошибок"]
    second.passport["environment"]["link_errors"] = copy.deepcopy(first.passport["environment"]["link_errors"])

    second.passport["measurement"]["channel_grid_rule"] = "different"
    assert comparability_reasons(first, profile, second, profile) == [
        "Область, окно или методика наблюдений и проверок"]
    second.passport["measurement"] = copy.deepcopy(first.passport["measurement"])
    changed_profile = copy.deepcopy(profile)
    changed_profile["metrics"]["P_zad"]["points"][1][0] += 1
    assert comparability_reasons(first, profile, second, changed_profile) == [
        "Шкалы нормализации или веса профиля Ω"]


def test_cancellation_keeps_pinned_input_but_does_not_store_partial_assessment(
    tmp_path: Path,
) -> None:
    with ZipFile(FIVE_EXPERIMENTS) as archive:
        names = archive.namelist()
        input_name = next(name for name in names if name.endswith("/experiment.json"))
        prefix = input_name.rsplit("/", 1)[0] + "/"
        folder = tmp_path / "cancelled-input"
        folder.mkdir()
        for name in names:
            if name.startswith(prefix):
                (folder / name.removeprefix(prefix)).write_bytes(archive.read(name))
        profile_name = next(name for name in names if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    experiment = read_folder(folder)
    store = DraftStore(tmp_path / "cancel.sqlite3")
    cancel = Event()
    progress: list[str] = []

    def request_at_k2(message: str) -> None:
        progress.append(message)
        if message == "Оценивание К2":
            cancel.set()

    with pytest.raises(CalculationCancelled):
        calculate_and_save(store, "cancelled", experiment, profile,
                           progress=request_at_k2, cancelled=cancel.is_set)
    assert progress[0] == "Проверка и фиксация входа и профиля Ω"
    assert store.list_drafts()
    assert store.list_assessments() == []
