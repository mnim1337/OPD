"""Checks for the seven fixed assessment scales and weights."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from .model import Diagnostic

METRICS = ("P_zad", "P_pr", "P_pot", "P_bal", "P_ust", "P_ad", "P_res")
DIRECTIONS = {"P_zad": "min", "P_pr": "max", "P_pot": "min", "P_bal": "max", "P_ust": "max", "P_ad": "min", "P_res": "min"}


def validate_profile(profile: Any) -> list[Diagnostic]:
    errors: list[Diagnostic] = []

    def error(code: str, place: str, message: str) -> None:
        errors.append(Diagnostic("error", code, place, "", message, "Исправьте профиль Ω"))

    if not isinstance(profile, dict):
        error("PROFILE_TYPE", "Ω", "ожидается объект")
        return errors
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        error("PROFILE_ID", "Ω.profile_id", "нужен идентификатор профиля")
    if not any(isinstance(profile.get(key), str) and profile[key].strip() for key in ("basis_ru", "note_ru")):
        error("PROFILE_BASIS", "Ω", "нужно описание оснований для шкал и весов")
    metrics = profile.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(METRICS):
        error("PROFILE_METRICS", "Ω.metrics", "нужны ровно семь фиксированных шкал")
        return errors
    total = Decimal(0)
    for key in METRICS:
        metric = metrics[key]
        place = f"Ω.metrics.{key}"
        if not isinstance(metric, dict):
            error("PROFILE_TYPE", place, "ожидается объект шкалы")
            continue
        if metric.get("direction") != DIRECTIONS[key]:
            error("PROFILE_DIRECTION", place, "направление не соответствует модели")
        try:
            weight = Decimal(str(metric.get("weight")))
            if not weight.is_finite() or weight <= 0:
                raise InvalidOperation
            total += weight
        except (InvalidOperation, ValueError):
            error("PROFILE_WEIGHT", place, "вес должен быть положительным конечным числом")
        points = metric.get("points")
        if not isinstance(points, list) or len(points) < 2:
            error("PROFILE_POINTS", place, "нужны хотя бы две опоры")
            continue
        previous_x = None
        previous_y = None
        for index, pair in enumerate(points):
            try:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise InvalidOperation
                x, y = (Decimal(str(value)) for value in pair)
                if not x.is_finite() or not y.is_finite() or not 0 <= y <= 1:
                    raise InvalidOperation
                if previous_x is not None and x <= previous_x:
                    error("PROFILE_ORDER", f"{place}.points[{index}]", "опоры X должны возрастать")
                if previous_y is not None:
                    if DIRECTIONS[key] == "max" and y < previous_y:
                        error("PROFILE_ORDER", f"{place}.points[{index}]", "шкала должна не убывать")
                    if DIRECTIONS[key] == "min" and y > previous_y:
                        error("PROFILE_ORDER", f"{place}.points[{index}]", "шкала должна не возрастать")
                previous_x, previous_y = x, y
            except (InvalidOperation, TypeError, ValueError):
                error("PROFILE_POINT", f"{place}.points[{index}]", "опора должна содержать два конечных числа")
        if previous_y is not None:
            try:
                first_y = Decimal(str(points[0][1]))
                last_y = Decimal(str(points[-1][1]))
                expected = (Decimal(1), Decimal(0)) if DIRECTIONS[key] == "min" else (Decimal(0), Decimal(1))
                if (first_y, last_y) != expected:
                    error("PROFILE_ENDS", place, "крайние опоры должны давать оценки 0 и 1 в принятом направлении")
            except (IndexError, InvalidOperation, TypeError, ValueError):
                pass
    if abs(total - 1) > Decimal("0.000000001"):
        error("PROFILE_SUM", "Ω.metrics", "сумма весов должна быть 1")
    return errors
