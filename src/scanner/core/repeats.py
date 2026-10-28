"""K3 descriptive statistics for distinct runs of one scenario, (3.53)–(3.55)."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any, Sequence

from scanner.io.model import Experiment

from .comparison import _stable, comparability_reasons
from .runner import calculate
from .scoring import resolve_profile

D = Decimal


def analyze_repeats(experiments: Sequence[Experiment], profile: dict[str, Any], *,
                    independence_basis: str | None = None) -> dict[str, Any]:
    """Calculate each run first; never infer independent repetitions from a series.

    A supplied basis records the caller's claim of independent random runs; it
    is not statistical verification. Student intervals require a separate
    normality assessment and are deliberately not calculated here.
    """
    if not experiments:
        raise ValueError("Нужен хотя бы один опыт")
    resolved = resolve_profile(profile)
    first = experiments[0]
    identifiers = [item.experiment_id for item in experiments]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Повторы должны иметь разные experiment_id")
    repeat_ids = [item.passport.get("repeat_id") for item in experiments]
    if any(value is None for value in repeat_ids) or len(set(repeat_ids)) != len(repeat_ids):
        raise ValueError("Повторы должны иметь разные заполненные repeat_id")
    scenario_id = first.passport.get("scenario_id")
    algorithm = _stable(first.passport.get("algorithm"))
    for item in experiments[1:]:
        if item.passport.get("scenario_id") != scenario_id:
            raise ValueError("Разные scenario_id не образуют повторы одного сценария")
        if _stable(item.passport.get("algorithm")) != algorithm:
            raise ValueError("Разные алгоритмы или настройки не образуют повторы")
        reasons = comparability_reasons(first, resolved, item, resolved)
        if reasons:
            raise ValueError("Несопоставимые условия повторов: " + "; ".join(reasons))
    with localcontext() as context:
        context.prec = 50
        results = [calculate(item, resolved) for item in experiments]
        if any(result.integral.status != "exact" for result in results):
            raise ValueError("Статистика К3 требует точных интегральных оценок всех повторов")
        values = [result.integral.exact_value for result in results]
        count = len(values)
        mean = sum(values, D(0)) / count
        variance = (sum(((value - mean) ** 2 for value in values), D(0)) / (count - 1)
                    if count >= 2 else None)
        deviation = variance.sqrt() if variance is not None else None
        basis = independence_basis.strip() if independence_basis else ""
        standard_error = deviation / D(count).sqrt() if basis and deviation is not None else None
        return {
            "scenario_id": scenario_id,
            "algorithm_id": first.passport["algorithm"]["algorithm_id"],
            "profile_id": results[0].profile_id,
            "model_version": results[0].model_version,
            "runs": [{"experiment_id": item.experiment_id, "repeat_id": item.passport["repeat_id"],
                      "integral_points": str(result.integral.exact_value)}
                     for item, result in zip(experiments, results, strict=True)],
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
