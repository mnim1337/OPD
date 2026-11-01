"""K3 descriptive statistics for distinct runs of one scenario, (3.53)–(3.55)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Sequence

from scanner.io.model import Experiment

from .comparison import _stable, comparability_reasons
from .runner import calculate
from .scoring import resolve_profile

D = Decimal
SavedRun = tuple[Experiment, dict[str, Any], dict[str, Any]]


def _algorithm_signature(experiment: Experiment) -> Any:
    algorithm = experiment.passport.get("algorithm", {})
    return _stable({key: algorithm.get(key) for key in (
        "algorithm_id", "implementation_version", "implementation_sha256",
        "routing_mode", "settings", "settings_completeness",
    )})


def _check_conditions(records: Sequence[SavedRun]) -> None:
    if not records:
        raise ValueError("Нужен хотя бы один опыт")
    experiments = [record[0] for record in records]
    first, first_result, first_profile = records[0]
    identifiers = [item.experiment_id for item in experiments]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Повторы должны иметь разные experiment_id")
    repeat_ids = [item.passport.get("repeat_id") for item in experiments]
    if any(value is None for value in repeat_ids) or len(set(repeat_ids)) != len(repeat_ids):
        raise ValueError("Повторы должны иметь разные заполненные repeat_id")
    scenario_id = first.passport.get("scenario_id")
    algorithm = _algorithm_signature(first)
    model_version = first_result.get("model_version")
    profile_id = first_profile.get("profile_id")
    if not model_version or not profile_id:
        raise ValueError("Для повторов нужны версии расчётной модели и профиля Ω")
    for item, result, profile in records:
        if item.passport.get("scenario_id") != scenario_id:
            raise ValueError("Разные scenario_id не образуют повторы одного сценария")
        if _algorithm_signature(item) != algorithm:
            raise ValueError("Разные алгоритмы или настройки не образуют повторы")
        if result.get("experiment_id") != item.experiment_id:
            raise ValueError("Сохранённая оценка не соответствует входу повтора")
        if result.get("profile_id") != profile.get("profile_id") or profile.get("profile_id") != profile_id:
            raise ValueError("Повторы требуют один профиль Ω")
        if result.get("model_version") != model_version:
            raise ValueError("Повторы требуют одну версию расчётной модели")
        reasons = comparability_reasons(first, first_profile, item, profile)
        if reasons:
            raise ValueError("Несопоставимые условия повторов: " + "; ".join(reasons))


def analyze_saved_repeats(records: Sequence[SavedRun], *,
                          independence_basis: str | None = None) -> dict[str, Any]:
    """Aggregate saved exact K2 results without recalculating observations.

    An independence basis is a caller's recorded claim, not its verification.
    No Student interval is returned without separate statistical assumptions.
    """
    _check_conditions(records)
    first, first_result, _ = records[0]
    with localcontext() as context:
        context.prec = 50
        values: list[Decimal] = []
        for item, result, _profile in records:
            integral = result.get("integral", {})
            if integral.get("status") != "exact" or integral.get("value") is None:
                raise ValueError("Статистика К3 требует точных интегральных оценок всех повторов")
            try:
                value = D(str(integral["value"]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"Неверная интегральная оценка повтора {item.experiment_id}") from exc
            if not value.is_finite():
                raise ValueError(f"Неконечная интегральная оценка повтора {item.experiment_id}")
            values.append(value)
        count = len(values)
        mean = sum(values, D(0)) / count
        variance = (sum(((value - mean) ** 2 for value in values), D(0)) / (count - 1)
                    if count >= 2 else None)
        deviation = variance.sqrt() if variance is not None else None
        basis = independence_basis.strip() if independence_basis else ""
        standard_error = deviation / D(count).sqrt() if basis and deviation is not None else None
        return {
            "scenario_id": first.passport.get("scenario_id"),
            "algorithm_id": first.passport["algorithm"]["algorithm_id"],
            "profile_id": first_result["profile_id"],
            "model_version": first_result["model_version"],
            "runs": [{"experiment_id": item.experiment_id, "repeat_id": item.passport["repeat_id"],
                      "integral_points": str(value)}
                     for (item, _result, _profile), value in zip(records, values, strict=True)],
            "count": count,
            "mean_points_3_53": str(mean),
            "sample_variance_points_squared_3_55": None if variance is None else str(variance),
            "sample_standard_deviation_points_3_55": None if deviation is None else str(deviation),
            "standard_error_points_3_55": None if standard_error is None else str(standard_error),
            "independence_basis": basis or None,
            "student_interval_3_56": None,
            "student_interval_reason": "Нужны обоснованные независимость, распределение и доверительный уровень; автоматически не устанавливаются.",
            "qualification": "Общий ряд или разные ID сами по себе не доказывают независимость опытов.",
        }


def analyze_repeats(experiments: Sequence[Experiment], profile: dict[str, Any], *,
                    independence_basis: str | None = None) -> dict[str, Any]:
    """Calculate each run separately, then aggregate its integral score."""
    if not experiments:
        raise ValueError("Нужен хотя бы один опыт")
    resolved = resolve_profile(profile)
    # Reject known condition mismatches before doing potentially expensive K0–K2 work.
    preview: list[SavedRun] = [
        (item, {"experiment_id": item.experiment_id, "profile_id": resolved["profile_id"],
                "model_version": "preview"}, resolved)
        for item in experiments
    ]
    _check_conditions(preview)
    records = [(item, calculate(item, resolved).as_dict(), resolved) for item in experiments]
    return analyze_saved_repeats(records, independence_basis=independence_basis)
