"""Headless entry point for a reproducible K0–K2 calculation."""

from __future__ import annotations

from decimal import localcontext
from typing import Any, Callable

from scanner.io.model import Diagnostic, Experiment
from scanner.io.validation import validate_experiment

from .indicators import calculate_raw
from .prepare import prepare
from .scoring import score
from .types import Assessment

MODEL_VERSION = "TZ-OAM-01-1.0-CH3-K0K2"


class CalculationError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]):
        super().__init__("\n".join(str(item) for item in diagnostics[:10]))
        self.diagnostics = diagnostics


class CalculationCancelled(Exception):
    """Raised at a safe calculation boundary when the caller requests cancellation."""


def _checkpoint(cancelled: Callable[[], bool] | None, progress: Callable[[str], None] | None,
                message: str) -> None:
    if progress is not None:
        progress(message)
    if cancelled is not None and cancelled():
        raise CalculationCancelled("Расчёт отменён до сохранения результата")


def calculate(experiment: Experiment, profile: dict[str, Any], *,
              progress: Callable[[str], None] | None = None,
              cancelled: Callable[[], bool] | None = None) -> Assessment:
    """Return all seven raw indicators, intermediate scores, groups and total.

    The function does not read files, call Qt, mutate input or save results.
    """
    diagnostics = validate_experiment(experiment, complete=False)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        raise CalculationError(errors)
    with localcontext() as context:
        context.prec = 50
        _checkpoint(cancelled, progress, "Подготовка модели К0")
        prepared = prepare(experiment)
        _checkpoint(cancelled, progress, "Расчёт показателей К1")
        raw, traces = calculate_raw(prepared)
        _checkpoint(cancelled, progress, "Оценивание К2")
        metrics, groups, integral = score(raw, traces, profile)
        _checkpoint(cancelled, progress, "Расчёт завершён; подготовка результата")
    warnings = [str(item) for item in diagnostics if item.severity == "warning"]
    if not prepared.exact_time:
        warnings.append("Временная погрешность не интерпретирована без согласования Q-04; зависящие от времени показатели неизвестны.")
    if profile.get("profile_status") == "demonstration_not_empirically_calibrated":
        warnings.append("Демонстрационный профиль Ω не является нормативом эффективности.")
    return Assessment(experiment.experiment_id, str(profile.get("profile_id", "")), MODEL_VERSION,
                      metrics, groups, integral, tuple(warnings))
