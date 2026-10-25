"""Coordinate immutable input snapshots, K0–K2 calculation and result storage."""

from __future__ import annotations

import uuid
from typing import Any, Callable

from scanner.io.model import Experiment
from scanner.io.profile import validate_profile
from scanner.io.storage import DraftStore
from scanner.io.validation import validate_experiment

from .runner import CalculationCancelled, CalculationError, calculate


class DispatchError(ValueError):
    pass


class ResultPersistenceError(OSError):
    def __init__(self, draft_id: str, revision: int, profile_id: str,
                 profile_version: int, model_version: str,
                 payload: dict[str, Any], assessment_id: str, cause: Exception):
        super().__init__(f"Расчёт завершён, но результат не сохранён: {cause}")
        self.draft_id = draft_id
        self.revision = revision
        self.profile_id = profile_id
        self.profile_version = profile_version
        self.model_version = model_version
        self.payload = payload
        self.assessment_id = assessment_id


def calculate_and_save(store: DraftStore, draft_id: str, experiment: Experiment,
                       profile: dict[str, Any], *,
                       progress: Callable[[str], None] | None = None,
                       cancelled: Callable[[], bool] | None = None) -> tuple[str, int, dict[str, Any]]:
    """Pin a valid input/profile, calculate that snapshot, then append its result."""
    diagnostics = validate_experiment(experiment, complete=False)
    errors = [item for item in diagnostics if item.severity == "error"]
    errors.extend(validate_profile(profile))
    if errors:
        raise DispatchError("\n".join(str(item) for item in errors[:30]))

    if progress is not None:
        progress("Проверка и фиксация входа и профиля Ω")
    if cancelled is not None and cancelled():
        raise CalculationCancelled("Расчёт отменён до фиксации входа")
    revision = store.save(draft_id, experiment, profile)
    pinned_experiment, pinned_profile, _ = store.load(draft_id, revision)
    profile_version = store.profile_version(draft_id, revision)
    if profile_version is None:
        raise DispatchError("Не удалось зафиксировать версию профиля Ω")

    assessment = calculate(pinned_experiment, pinned_profile, progress=progress, cancelled=cancelled)
    payload = assessment.as_dict()
    payload["input_revision"] = revision
    payload["profile_version"] = profile_version[1]
    payload["profile"] = pinned_profile
    assessment_id = str(uuid.uuid4())
    if progress is not None:
        progress("Сохранение результата")
    if cancelled is not None and cancelled():
        raise CalculationCancelled("Расчёт отменён до сохранения результата")
    payload["assessment_id"] = assessment_id
    try:
        assessment_id = store.save_assessment(
            draft_id, revision, profile_version[0], profile_version[1],
            assessment.model_version, payload, assessment_id,
        )
    except Exception as exc:
        raise ResultPersistenceError(
            draft_id, revision, profile_version[0], profile_version[1],
            assessment.model_version, payload, assessment_id, exc,
        ) from exc
    return assessment_id, revision, payload


__all__ = ["CalculationError", "DispatchError", "ResultPersistenceError", "calculate_and_save"]
