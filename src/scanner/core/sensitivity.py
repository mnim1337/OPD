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
    resolved = resolve_profile(profile)
    reasons = comparability_reasons(first, resolved, second, resolved)
    if reasons:
        raise ValueError("Опыты несопоставимы: " + "; ".join(reasons))
    left = calculate(first, resolved).as_dict()
    right = calculate(second, resolved).as_dict()
    return analyze_saved_sensitivity(first, left, resolved, second, right, resolved, epsilon_weight)


def _exact(payload: dict[str, Any], description: str) -> Decimal:
    if payload.get("status") != "exact" or payload.get("value") is None:
        raise ValueError(f"К3 требует точного значения: {description}")
    try:
        value = D(str(payload["value"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"К3 получил неверное число: {description}") from exc
    if not value.is_finite():
        raise ValueError(f"К3 требует конечного числа: {description}")
    return value


def analyze_saved_sensitivity(first: Experiment, first_result: dict[str, Any],
                              first_profile: dict[str, Any], second: Experiment,
                              second_result: dict[str, Any], second_profile: dict[str, Any],
                              epsilon_weight: Decimal | int | str = 0) -> dict[str, Any]:
    """Analyze persisted K2 results without recalculating observations or changing history."""
    radius = _nonnegative_number(epsilon_weight)
    left_profile = resolve_profile(first_profile)
    right_profile = resolve_profile(second_profile)
    reasons = comparability_reasons(first, left_profile, second, right_profile)
    if reasons:
        raise ValueError("Опыты несопоставимы: " + "; ".join(reasons))
    if first_result.get("model_version") != second_result.get("model_version"):
        raise ValueError("К3 требует одинаковой версии расчётной модели")
    if (first_result.get("profile_id") != left_profile.get("profile_id") or
            second_result.get("profile_id") != right_profile.get("profile_id")):
        raise ValueError("Сохранённая оценка не соответствует выбранному профилю Ω")
    if first_result.get("experiment_id") != first.experiment_id or second_result.get("experiment_id") != second.experiment_id:
        raise ValueError("Сохранённая оценка не соответствует выбранному входу")
    with localcontext() as context:
        context.prec = 50
        left_total = _exact(first_result["integral"], "итог первого опыта")
        right_total = _exact(second_result["integral"], "итог второго опыта")
        indicator_terms: dict[str, dict[str, str]] = {}
        raw_bound = D(0)
        normalized_delta = D(0)
        for key in METRICS:
            a = first_result["metrics"][key]
            b = second_result["metrics"][key]
            left_raw = _exact(a["raw"], f"физический показатель {key} первого опыта")
            right_raw = _exact(b["raw"], f"физический показатель {key} второго опыта")
            left_normal = _exact(a["normalized"], f"нормализация {key} первого опыта")
            right_normal = _exact(b["normalized"], f"нормализация {key} второго опыта")
            weight = D(str(left_profile["metrics"][key]["weight"]))
            if D(str(a["weight"])) != weight or D(str(b["weight"])) != weight:
                raise ValueError(f"Сохранённый вес {key} не соответствует выбранному профилю Ω")
            slope = max_scale_slope(left_profile["metrics"][key]["points"])
            physical_change = abs(left_raw - right_raw)
            term = D(100) * weight * slope * physical_change
            raw_bound += term
            normalized_delta += D(100) * weight * (left_normal - right_normal)
            indicator_terms[key] = {
                "slope_per_unit": str(slope),
                "physical_change": str(physical_change),
                "contribution_to_bound_points": str(term),
                "unit": a["raw"]["unit"],
            }
        actual_delta = left_total - right_total
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
            "model_version": first_result["model_version"],
            "profile_id": first_result["profile_id"],
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
