"""Finite and one-sided values, preserving units and bound openness."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

Status = Literal["exact", "interval", "unknown", "not_applicable"]


@dataclass(frozen=True)
class Value:
    status: Status
    lower: Decimal | None = None
    upper: Decimal | None = None
    lower_open: bool = False
    upper_open: bool = False
    unit: str = "1"
    reason: str = ""

    @property
    def exact_value(self) -> Decimal | None:
        return self.lower if self.status == "exact" else None

    @classmethod
    def exact(cls, value: Decimal | int | str, unit: str = "1") -> Value:
        number = Decimal(str(value))
        return cls("exact", number, number, unit=unit)

    @classmethod
    def interval(cls, lower: Decimal | int | str, upper: Decimal | int | str | None,
                 unit: str = "1", *, lower_open: bool = False, upper_open: bool = False,
                 reason: str = "") -> Value:
        lo = Decimal(str(lower))
        hi = None if upper is None else Decimal(str(upper))
        if hi is not None and lo > hi:
            raise ValueError("Нижняя граница больше верхней")
        if hi == lo and not lower_open and not upper_open:
            return cls.exact(lo, unit)
        return cls("interval", lo, hi, lower_open, upper_open, unit, reason)

    @classmethod
    def unknown(cls, unit: str = "1", reason: str = "") -> Value:
        return cls("unknown", unit=unit, reason=reason)

    @classmethod
    def not_applicable(cls, unit: str = "1", reason: str = "") -> Value:
        return cls("not_applicable", unit=unit, reason=reason)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "value": None if self.exact_value is None else str(self.exact_value),
            "lower": None if self.lower is None else str(self.lower),
            "upper": None if self.upper is None else str(self.upper),
            "lower_open": self.lower_open,
            "upper_open": self.upper_open,
            "upper_unbounded": self.status == "interval" and self.upper is None,
            "unit": self.unit,
            "reason": self.reason or None,
        }


@dataclass(frozen=True)
class MetricResult:
    key: str
    symbol: str
    direction: str
    raw: Value
    normalized: Value
    partial_score: Value
    weight: Decimal
    contribution: Value
    trace: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol, "direction": self.direction,
            "raw": self.raw.as_dict(), "normalized": self.normalized.as_dict(),
            "partial_score": self.partial_score.as_dict(),
            "weight": str(self.weight), "contribution": self.contribution.as_dict(),
            "trace": _jsonable(self.trace),
        }


@dataclass(frozen=True)
class Assessment:
    experiment_id: str
    profile_id: str
    model_version: str
    metrics: dict[str, MetricResult]
    groups: dict[str, Value]
    integral: Value
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "profile_id": self.profile_id,
            "model_version": self.model_version,
            "metrics": {key: result.as_dict() for key, result in self.metrics.items()},
            "groups": {key: value.as_dict() for key, value in self.groups.items()},
            "integral": self.integral.as_dict(),
            "warnings": list(self.warnings),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
