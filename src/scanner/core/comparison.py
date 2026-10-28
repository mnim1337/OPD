"""Check whether saved assessments share the same evaluation conditions."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from scanner.io.model import Experiment


def _stable(value: Any) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, int):
        return str(Decimal(value).normalize())
    if isinstance(value, float):
        try:
            return str(Decimal(str(value)).normalize())
        except InvalidOperation:
            return str(value)
    if isinstance(value, dict):
        return {str(key): _stable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    return str(value)


def _signature(experiment: Experiment, profile: dict[str, Any]) -> dict[str, Any]:
    passport = experiment.passport
    environment = passport.get("environment", {})
    events = passport.get("scenario", {}).get("events", [])
    event_conditions = [
        {key: _stable(event.get(key)) for key in (
            "event_id", "event_type", "object_type", "object_ids", "scheduled_time_s", "changes",
        )}
        for event in events
    ]
    return {
        "network": _stable(passport.get("network", {})),
        "traffic": _stable(passport.get("traffic", {})),
        "environment": _stable({
            key: environment.get(key) for key in (
                "execution_source", "pak_version", "time_mode", "hardware_description",
                "queue_discipline", "buffer_limit_bytes", "link_errors",
            )
        } | {"randomness_used": environment.get("randomness", {}).get("used")}),
        # Distinct random seeds are expected between independent repeated runs.
        # Actual execution time and success are observations; compare the planned intervention.
        "events": event_conditions,
        "measurement": _stable(passport.get("measurement", {})),
        "scales_and_weights": _stable(profile.get("metrics", {})),
    }


def comparability_reasons(first: Experiment, first_profile: dict[str, Any],
                          second: Experiment, second_profile: dict[str, Any]) -> list[str]:
    """Return conditions that differ; route, channel outcomes and packet losses are excluded."""
    labels = {
        "network": "Состав или исходное состояние сети",
        "traffic": "Заданный состав или генерация потоков",
        "environment": "Условия выполнения, очереди, буферы или модель ошибок",
        "events": "Внешние события и их расписание",
        "measurement": "Область, окно или методика наблюдений и проверок",
        "scales_and_weights": "Шкалы нормализации или веса профиля Ω",
    }
    left = _signature(first, first_profile)
    right = _signature(second, second_profile)
    return [labels[key] for key in labels if left[key] != right[key]]

