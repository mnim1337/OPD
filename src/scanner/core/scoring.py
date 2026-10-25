"""K2: equations (3.32)–(3.45), including expert weights and bounds."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from scanner.io.profile import METRICS, validate_profile

from .indicators import DIRECTIONS, SYMBOLS, UNITS
from .types import MetricResult, Value

D = Decimal
GROUPS = {
    "I_1": ("P_zad", "P_pr", "P_pot"),
    "I_2": ("P_bal",),
    "I_3": ("P_ust", "P_ad"),
    "I_4": ("P_res",),
}


def expert_weights(experts: list[dict[str, Any]]) -> dict[str, Decimal]:
    """Equation (3.36): normalize each expert first, then average vectors."""
    if not experts:
        raise ValueError("Нужна хотя бы одна экспертная оценка")
    weights = {key: D(0) for key in METRICS}
    for index, expert in enumerate(experts, 1):
        scores = expert.get("scores") if isinstance(expert, dict) else None
        if not isinstance(scores, dict) or set(scores) != set(METRICS):
            raise ValueError(f"Эксперт {index}: нужны оценки семи показателей")
        values = {}
        for key in METRICS:
            try:
                value = D(str(scores[key]))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError(f"Эксперт {index}, {key}: неверная оценка") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError(f"Эксперт {index}, {key}: нужна положительная конечная оценка")
            values[key] = value
        total = sum(values.values(), D(0))
        for key in METRICS:
            weights[key] += values[key] / total / len(experts)
    return weights


def resolve_profile(profile: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(profile)
    experts = result.get("experts")
    if experts is not None:
        derived = expert_weights(experts)
        for key, weight in derived.items():
            supplied = result["metrics"][key].get("weight")
            if supplied is not None:
                try:
                    difference = abs(D(str(supplied)) - weight)
                except (InvalidOperation, ValueError) as exc:
                    raise ValueError(f"{key}: неверный сохранённый вес") from exc
                if difference > D("0.000000001"):
                    raise ValueError(f"{key}: вес не совпадает с экспертным нормированием (3.36)")
            result["metrics"][key]["weight"] = weight
    errors = validate_profile(result)
    if errors:
        raise ValueError(str(errors[0]))
    return result


def _scale(value: Decimal | None, points: list[list[Any]]) -> Decimal:
    prepared = [(D(str(x)), D(str(y))) for x, y in points]
    if value is None:  # +infinity reaches the last saturation segment.
        return prepared[-1][1]
    if value <= prepared[0][0]:
        return prepared[0][1]
    if value >= prepared[-1][0]:
        return prepared[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(prepared, prepared[1:]):
        if left_x <= value <= right_x:
            return left_y + (right_y - left_y) * (value - left_x) / (right_x - left_x)
    raise ArithmeticError("Опора для значения не найдена")


def normalize(raw: Value, metric: dict[str, Any]) -> Value:
    if raw.status == "not_applicable":
        return Value.not_applicable("1", raw.reason)
    if raw.status == "unknown":
        return Value.interval(0, 1, "1", reason="Существующий показатель полностью неизвестен.")
    if raw.lower is None:
        return Value.interval(0, 1, "1", reason="Нижняя граница показателя отсутствует.")
    endpoints = [_scale(raw.lower, metric["points"]), _scale(raw.upper, metric["points"])]
    return Value.interval(min(endpoints), max(endpoints), "1", reason=raw.reason)


def _multiply(value: Value, factor: Decimal, unit: str = "point") -> Value:
    if value.status == "not_applicable":
        return Value.not_applicable(unit, value.reason)
    if value.status == "unknown" or value.lower is None:
        return Value.unknown(unit, value.reason)
    return Value.interval(value.lower * factor, None if value.upper is None else value.upper * factor,
                          unit, reason=value.reason)


def score(raw: dict[str, Value], traces: dict[str, dict[str, Any]], profile: dict[str, Any]) -> tuple[dict[str, MetricResult], dict[str, Value], Value]:
    profile = resolve_profile(profile)
    metrics: dict[str, MetricResult] = {}
    for key, symbol, unit, direction in zip(METRICS, SYMBOLS, UNITS, DIRECTIONS, strict=True):
        value = raw[key]
        scale = profile["metrics"][key]
        normal = normalize(value, scale)
        weight = D(str(scale["weight"]))
        metrics[key] = MetricResult(
            key=key, symbol=symbol, direction=direction, raw=value, normalized=normal,
            partial_score=_multiply(normal, D(100)), weight=weight,
            contribution=_multiply(normal, D(100) * weight), trace=traces[key],
        )
    groups: dict[str, Value] = {}
    for name, members in GROUPS.items():
        if any(metrics[key].contribution.status == "not_applicable" for key in members):
            groups[name] = Value.not_applicable("point", "В группе есть неприменимый показатель.")
            continue
        group_weight = sum((metrics[key].weight for key in members), D(0))
        lower = sum((metrics[key].contribution.lower for key in members), D(0))
        upper = sum((metrics[key].contribution.upper for key in members), D(0))
        groups[name] = Value.interval(lower / group_weight, upper / group_weight, "point")
    if any(result.contribution.status == "not_applicable" for result in metrics.values()):
        integral = Value.not_applicable("point", "Хотя бы один исходный показатель неприменим.")
    else:
        integral = Value.interval(
            sum((result.contribution.lower for result in metrics.values()), D(0)),
            sum((result.contribution.upper for result in metrics.values()), D(0)),
            "point",
        )
    return metrics, groups, integral
