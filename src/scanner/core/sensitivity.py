"""K3 sensitivity bounds from TZ-OAM-01, equations (3.47)–(3.52)."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from scanner.io.model import Experiment
from scanner.io.profile import METRICS

from .comparison import comparability_reasons
from .runner import calculate
from .scoring import resolve_profile

D = Decimal


def _nonnegative_number(value: Decimal | int | str) -> Decimal:
    try:
        number = D(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Радиус изменения весов должен быть конечным числом") from exc
    if not number.is_finite() or not D(0) <= number <= D(2):
        raise ValueError("Радиус изменения весов должен быть от 0 до 2")
    return number


def max_scale_slope(points: list[list[Any]]) -> Decimal:
    """Maximum absolute slope L_i, equation (3.47); x retains its physical unit."""
    if len(points) < 2:
        raise ValueError("Для наклона нужны хотя бы две опоры")
    pairs = [(D(str(x)), D(str(y))) for x, y in points]
    if any(not x.is_finite() or not y.is_finite() for x, y in pairs):
        raise ValueError("Опоры шкалы должны быть конечными")
    slopes = []
    for (left_x, left_y), (right_x, right_y) in zip(pairs, pairs[1:]):
        if right_x <= left_x:
            raise ValueError("Опоры X должны строго возрастать")
        slopes.append(abs((right_y - left_y) / (right_x - left_x)))
    return max(slopes)


def analyze_sensitivity(first: Experiment, second: Experiment,
                        profile: dict[str, Any], epsilon_weight: Decimal | int | str = 0) -> dict[str, Any]:
    """Analyze two comparable, exact evaluations under one fixed scale and weights.

    epsilon_weight is an L1 radius of admissible nonnegative weight vectors
    with unit sum. A failed sufficient condition is reported as inconclusive.
    """
    radius = _nonnegative_number(epsilon_weight)
    resolved = resolve_profile(profile)
    reasons = comparability_reasons(first, resolved, second, resolved)
    if reasons:
        raise ValueError("Опыты несопоставимы: " + "; ".join(reasons))
    with localcontext() as context:
        context.prec = 50
        left = calculate(first, resolved)
        right = calculate(second, resolved)
        if left.integral.status != "exact" or right.integral.status != "exact":
            raise ValueError("К3 требует точных итоговых оценок обоих опытов")
        indicator_terms: dict[str, dict[str, str]] = {}
        raw_bound = D(0)
        normalized_delta = D(0)
        for key in METRICS:
            a = left.metrics[key]
            b = right.metrics[key]
            if a.raw.status != "exact" or b.raw.status != "exact":
                raise ValueError(f"К3 требует точного физического показателя {key} в обоих опытах")
            if a.normalized.status != "exact" or b.normalized.status != "exact":
                raise ValueError(f"К3 требует точной нормализации {key} в обоих опытах")
            slope = max_scale_slope(resolved["metrics"][key]["points"])
            physical_change = abs(a.raw.exact_value - b.raw.exact_value)
            term = D(100) * a.weight * slope * physical_change
            raw_bound += term
            normalized_delta += D(100) * a.weight * (a.normalized.exact_value - b.normalized.exact_value)
            indicator_terms[key] = {
                "slope_per_unit": str(slope),
                "physical_change": str(physical_change),
                "contribution_to_bound_points": str(term),
                "unit": a.raw.unit,
            }
        actual_delta = left.integral.exact_value - right.integral.exact_value
        if abs(actual_delta - normalized_delta) > D("1e-40"):
            raise ArithmeticError("Разность К3 не согласуется с сохранёнными вкладами К2")
        weight_bound = D(50) * radius
        comparison_bound = D(100) * radius
        if actual_delta > comparison_bound:
            conclusion = "first_remains_higher"
        elif actual_delta < -comparison_bound:
            conclusion = "second_remains_higher"
        else:
            conclusion = "inconclusive"
        return {
            "first_experiment_id": first.experiment_id,
            "second_experiment_id": second.experiment_id,
            "model_version": left.model_version,
            "profile_id": left.profile_id,
            "epsilon_weight_l1": str(radius),
            "indicators": indicator_terms,
            "physical_change_bound_points_3_48": str(raw_bound),
            "weight_change_bound_points_3_49": str(weight_bound),
            "joint_change_bound_points_3_50": str(raw_bound + weight_bound),
            "score_difference_points_3_51": str(actual_delta),
            "difference_change_bound_points_3_52": str(comparison_bound),
            "conclusion": conclusion,
            "qualification": "Достаточное условие; inconclusive не доказывает смену предпочтения.",
        }
