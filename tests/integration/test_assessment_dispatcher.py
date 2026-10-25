from __future__ import annotations

import json
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest

from scanner.core import dispatcher
from scanner.core.dispatcher import ResultPersistenceError, calculate_and_save
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
    backup_path = tmp_path / "saved-assessment-backup.sqlite3"
    store.backup(backup_path)
    assert DraftStore(backup_path).load_assessment(assessment_id)[0] == calculated

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


def test_backup_is_consistent_and_cannot_overwrite_active_store(tmp_path: Path) -> None:
    store = DraftStore(tmp_path / "drafts.sqlite3")
    with pytest.raises(ValueError):
        store.backup(store.path)
    backup = tmp_path / "backup.sqlite3"
    store.backup(backup)
    assert backup.exists()
    assert DraftStore(backup).list_assessments() == []
