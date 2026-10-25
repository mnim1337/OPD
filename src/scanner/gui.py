"""Qt application for editing inputs and running saved K0–K2 assessments."""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import sys
from threading import Event
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, Signal, QThread
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QScrollArea,
    QPushButton, QProgressDialog, QTableView, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QFileDialog, QTableWidget, QTableWidgetItem, QStackedWidget,
)

from .io.format import DataError, export_four, from_manual, read_folder
from .io.model import Experiment, HEADERS, file_contract
from .io.profile import validate_profile
from .io.profile import DIRECTIONS, METRICS
from .io.storage import DraftStore
from .io.validation import validate_experiment
from .core.comparison import comparability_reasons
from .core.dispatcher import DispatchError, ResultPersistenceError, calculate_and_save
from .core.runner import CalculationCancelled
from .core.prepare import prepare


METRIC_LABELS = {
    "P_zad": "Pзад", "P_pr": "Pпр", "P_pot": "Pпот", "P_bal": "Pбал",
    "P_ust": "Pуст", "P_ad": "Pад", "P_res": "Pрес",
}
CHART_COLORS = [QColor("#2374AB"), QColor("#F18F01"), QColor("#C73E1D"),
                QColor("#3B8C6E"), QColor("#8055A2"), QColor("#D65DB1"), QColor("#65737E")]


class AssessmentWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, store: DraftStore, draft_id: str, experiment: Any,
                 profile: dict[str, Any], cancel_requested: Event):
        super().__init__()
        self.store = store
        self.draft_id = draft_id
        self.experiment = experiment
        self.profile = profile
        self.cancel_requested = cancel_requested

    def run(self) -> None:
        try:
            result = calculate_and_save(
                self.store, self.draft_id, self.experiment, self.profile,
                progress=self.progress.emit, cancelled=self.cancel_requested.is_set,
            )
            self.succeeded.emit(result)
        except Exception as exc:
            self.failed.emit(exc)


def _display_number(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return format(Decimal(str(value)), ".8g")
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


class RadarChart(QWidget):
    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.metrics = payload.get("metrics", {})
        self.setMinimumSize(340, 330)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        center = QPointF(self.width() / 2, self.height() / 2 + 4)
        radius = min(self.width(), self.height()) * 0.32
        keys = list(METRIC_LABELS)
        angles = [-math.pi / 2 + index * 2 * math.pi / len(keys) for index in range(len(keys))]
        painter.setPen(QPen(QColor("#CBD2D9"), 1))
        for fraction in (0.25, 0.5, 0.75, 1.0):
            points = [QPointF(center.x() + radius * fraction * math.cos(angle),
                              center.y() + radius * fraction * math.sin(angle)) for angle in angles]
            painter.drawPolygon(QPolygonF(points))
            painter.drawText(QPointF(center.x() + 4, center.y() - radius * fraction + 14),
                             str(round(fraction * 100)))
        values: list[float | None] = []
        for index, key in enumerate(keys):
            angle = angles[index]
            end = QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle))
            painter.drawLine(center, end)
            label_rect = QRectF(end.x() - 43, end.y() - 14, 86, 25)
            painter.setPen(QPen(QColor("#273444"), 1))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, METRIC_LABELS[key])
            score = self.metrics.get(key, {}).get("partial_score", {})
            if score.get("status") == "exact" and score.get("value") is not None:
                try:
                    values.append(float(score["value"]))
                except (TypeError, ValueError):
                    values.append(None)
            elif score.get("status") == "interval":
                try:
                    lower = float(score["lower"])
                    upper = float(score["upper"]) if score.get("upper") is not None else 100.0
                    painter.setPen(QPen(QColor("#8055A2"), 3))
                    painter.drawLine(
                        QPointF(center.x() + radius * lower / 100 * math.cos(angle),
                                center.y() + radius * lower / 100 * math.sin(angle)),
                        QPointF(center.x() + radius * upper / 100 * math.cos(angle),
                                center.y() + radius * upper / 100 * math.sin(angle)),
                    )
                    values.append(None)
                except (TypeError, ValueError):
                    values.append(None)
            else:
                values.append(None)
        if len(values) == len(keys) and all(value is not None for value in values):
            polygon = QPolygonF([
                QPointF(center.x() + radius * min(100, max(0, value)) / 100 * math.cos(angle),
                        center.y() + radius * min(100, max(0, value)) / 100 * math.sin(angle))
                for value, angle in zip(values, angles, strict=True)
            ])
            painter.setBrush(QBrush(QColor(35, 116, 171, 45)))
            painter.setPen(QPen(QColor("#2374AB"), 2))
            painter.drawPolygon(polygon)
        else:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#2374AB"), 2))
            for index, value in enumerate(values):
                next_index = (index + 1) % len(values)
                if value is None or values[next_index] is None:
                    continue
                first = QPointF(center.x() + radius * min(100, max(0, value)) / 100 * math.cos(angles[index]),
                                center.y() + radius * min(100, max(0, value)) / 100 * math.sin(angles[index]))
                second_value = values[next_index]
                second = QPointF(center.x() + radius * min(100, max(0, second_value)) / 100 * math.cos(angles[next_index]),
                                 center.y() + radius * min(100, max(0, second_value)) / 100 * math.sin(angles[next_index]))
                painter.drawLine(first, second)
                painter.setBrush(QColor("#2374AB"))
                painter.drawEllipse(first, 4, 4)
        painter.setPen(QPen(QColor("#273444"), 1))
        painter.drawText(QRectF(0, 5, self.width(), 25), Qt.AlignmentFlag.AlignCenter,
                         "Частные баллы Bᵢ(A), 0–100")
        painter.end()


class ContributionChart(QWidget):
    def __init__(self, payload: dict[str, Any], parent: QWidget | None = None):
        super().__init__(parent)
        self.metrics = payload.get("metrics", {})
        self.integral = payload.get("integral", {})
        self.setMinimumSize(340, 330)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        left, right = 24, max(25, self.width() - 24)
        width = right - left
        label = self.integral.get("value")
        if label is None:
            lower = self.integral.get("lower")
            upper = self.integral.get("upper")
            total_text = (f"I(A) ∈ [{_display_number(lower)}, {_display_number(upper) if upper is not None else '∞'}] баллов"
                          if self.integral.get("status") == "interval" else
                          f"I(A): {self.integral.get('status', 'unknown')}")
        else:
            total_text = f"I(A) = {_display_number(label)} баллов"
        painter.setPen(QPen(QColor("#273444"), 1))
        painter.drawText(QRectF(left, 12, width, 28), Qt.AlignmentFlag.AlignCenter, total_text)
        if self.integral.get("status") == "not_applicable":
            painter.setPen(QPen(QColor("#59636E"), 1))
            painter.drawText(QRectF(left, 55, width, 70), Qt.AlignmentFlag.AlignCenter,
                             "Полная оценка неприменима; общая полоса не строится.")
            painter.end()
            return
        y, height = 78, 44
        painter.setPen(QPen(QColor("#CBD2D9"), 1))
        painter.setBrush(QColor("#F3F5F7"))
        painter.drawRect(left, y, width, height)
        lower_sum = 0.0
        upper_sum = 0.0
        exact = True
        for key in METRIC_LABELS:
            part = self.metrics.get(key, {}).get("contribution", {})
            if part.get("lower") is None:
                exact = False
                continue
            try:
                low = float(part["lower"])
                high = float(part["upper"]) if part.get("upper") is not None else 100.0
            except (TypeError, ValueError):
                exact = False
                continue
            start_x = left + width * min(100.0, max(0.0, lower_sum)) / 100
            end_x = left + width * min(100.0, max(0.0, lower_sum + low)) / 100
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(CHART_COLORS[list(METRIC_LABELS).index(key)])
            painter.drawRect(QRectF(start_x, y, max(0, end_x - start_x), height))
            lower_sum += low
            upper_sum += high
            exact = exact and part.get("status") == "exact"
        if not exact and upper_sum > lower_sum:
            start_x = left + width * min(100.0, max(0.0, lower_sum)) / 100
            end_x = left + width * min(100.0, max(0.0, upper_sum)) / 100
            painter.setPen(QPen(QColor("#8055A2"), 2))
            painter.setBrush(QBrush(QColor("#E8DDF0"), Qt.BrushStyle.BDiagPattern))
            painter.drawRect(QRectF(start_x, y, max(0, end_x - start_x), height))
        painter.setPen(QPen(QColor("#52616B"), 1))
        painter.drawLine(left, y + height + 9, right, y + height + 9)
        for tick in (0, 25, 50, 75, 100):
            x = left + width * tick / 100
            painter.drawLine(QPointF(x, y + height + 7), QPointF(x, y + height + 12))
            painter.drawText(QRectF(x - 18, y + height + 12, 36, 20), Qt.AlignmentFlag.AlignCenter, str(tick))
        for index, key in enumerate(METRIC_LABELS):
            column, row = index % 2, index // 2
            x = left + column * (width / 2)
            yy = 185 + row * 32
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(CHART_COLORS[index])
            painter.drawRect(QRectF(x, yy + 5, 13, 13))
            painter.setPen(QPen(QColor("#273444"), 1))
            value = self.metrics.get(key, {}).get("contribution", {})
            shown = (_display_number(value.get("value")) if value.get("value") is not None else
                     value.get("status", "unknown"))
            painter.drawText(QRectF(x + 19, yy, width / 2 - 18, 25), Qt.AlignmentFlag.AlignLeft,
                             f"{METRIC_LABELS[key]}: {shown}")
        painter.end()


class TimelineChart(QWidget):
    def __init__(self, experiment: Experiment, parent: QWidget | None = None):
        super().__init__(parent)
        self.experiment = experiment
        self.context = prepare(experiment)
        self.flow_filter: str | None = None
        self.link_filter: str | None = None
        self.time_window = (float(self.context.capture_start), float(self.context.capture_end))
        self.setMinimumWidth(700)
        self._resize_to_rows()

    def set_view(self, flow_id: str | None = None, link_id: str | None = None,
                 time_window: tuple[float, float] | None = None) -> None:
        self.flow_filter = flow_id
        self.link_filter = link_id
        if time_window is not None and time_window[0] < time_window[1]:
            self.time_window = time_window
        self._resize_to_rows()
        self.update()

    def _rows(self) -> list[tuple[str, str, list[dict[str, Any]]]]:
        rows = []
        for flow_id in self.context.flows:
            if self.flow_filter and flow_id != self.flow_filter:
                continue
            rows.append(("route", flow_id, self.context.by_flow.get(flow_id, [])))
        for link_id in self.context.links:
            if self.link_filter and link_id != self.link_filter:
                continue
            rows.append(("channel", link_id, self.context.by_link.get(link_id, [])))
        return rows

    def _resize_to_rows(self) -> None:
        self.setMinimumHeight(max(260, 95 + 31 * len(self._rows())))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        left, right = 175, max(176, self.width() - 28)
        top, lane_h = 54, 31
        start, end = self.time_window
        if start >= end:
            painter.end()
            return
        scale = (right - left) / (end - start)
        painter.setPen(QPen(QColor("#7D8790"), 1))
        for fraction in (0, .25, .5, .75, 1):
            x = left + (right - left) * fraction
            painter.drawLine(QPointF(x, top - 8), QPointF(x, self.height() - 30))
            time_value = start + (end - start) * fraction
            painter.drawText(QRectF(x - 35, 13, 70, 22), Qt.AlignmentFlag.AlignCenter, f"{time_value:.3g} s")
        state_colors = {
            "working": QColor("#67B88A"), "broken": QColor("#D9534F"),
            "no_route": QColor("#89939B"), "loop": QColor("#9467BD"),
            "up": QColor("#67B88A"), "down": QColor("#D9534F"),
        }
        for index, (kind, name, segments) in enumerate(self._rows()):
            y = top + index * lane_h
            painter.setPen(QPen(QColor("#273444"), 1))
            painter.drawText(QRectF(8, y, left - 16, lane_h - 2), Qt.AlignmentFlag.AlignVCenter,
                             f"{name} · {'маршрут' if kind == 'route' else 'канал'}")
            for segment in segments:
                a = max(start, float(segment["time_start_s"]))
                b = min(end, float(segment["time_end_s"]))
                if a >= b:
                    continue
                x = left + (a - start) * scale
                w = max(1, (b - a) * scale)
                if kind == "route":
                    state = segment.get("route_state")
                    if state == "installed":
                        path = segment.get("path_links") or []
                        boundaries = {segment["time_start_s"], segment["time_end_s"]}
                        try:
                            boundaries.update(self.context._state_boundaries(segment["time_start_s"],
                                                                             segment["time_end_s"]))
                            ordered = sorted(boundaries)
                            for left_time, right_time in zip(ordered, ordered[1:]):
                                left_float, right_float = max(a, float(left_time)), min(b, float(right_time))
                                if left_float >= right_float:
                                    continue
                                state_at = self.context.physical_at(path, left_time)
                                color = state_colors["working"] if state_at is True else \
                                    state_colors["broken"] if state_at is False else QColor("#E4C95C")
                                painter.setPen(QPen(QColor("#FFFFFF"), .6))
                                painter.setBrush(QBrush(color) if state_at is not None else
                                                 QBrush(color, Qt.BrushStyle.BDiagPattern))
                                painter.drawRect(QRectF(left + (left_float - start) * scale, y + 5,
                                                        max(1, (right_float - left_float) * scale), lane_h - 10))
                        except (KeyError, TypeError, ValueError):
                            painter.setPen(QPen(QColor("#FFFFFF"), .6))
                            painter.setBrush(QBrush(QColor("#E4C95C"), Qt.BrushStyle.BDiagPattern))
                            painter.drawRect(QRectF(x, y + 5, w, lane_h - 10))
                        continue
                    color = state_colors.get(state, QColor("#E4C95C"))
                    unknown = state not in state_colors
                else:
                    up = segment.get("link_up")
                    known = segment.get("observation_status") == "complete" and isinstance(up, bool)
                    color = state_colors["up"] if known and up else state_colors["down"] if known else QColor("#E4C95C")
                    unknown = not known
                painter.setPen(QPen(QColor("#FFFFFF"), .6))
                painter.setBrush(QBrush(color, Qt.BrushStyle.BDiagPattern) if unknown else QBrush(color))
                painter.drawRect(QRectF(x, y + 5, w, lane_h - 10))
        event_bottom = top + len(self._rows()) * lane_h
        for marker in self.experiment.passport.get("scenario", {}).get("events", []):
            instant = marker.get("actual_time_s")
            if marker.get("result") != "executed" or instant is None:
                continue
            value = float(instant)
            if start <= value <= end:
                x = left + (value - start) * scale
                painter.setPen(QPen(QColor("#C0392B"), 2, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, top - 8), QPointF(x, event_bottom))
                painter.save()
                painter.translate(x + 3, top - 10)
                painter.rotate(-45)
                painter.drawText(QPointF(0, 0), str(marker.get("event_type", "event")))
                painter.restore()
        painter.setPen(QPen(QColor("#65737E"), 1))
        painter.drawText(QRectF(left, self.height() - 25, right - left, 20),
                         Qt.AlignmentFlag.AlignCenter, "модельное время опыта")
        painter.end()


class AssessmentDialog(QDialog):
    def __init__(self, payload: dict[str, Any], saved: bool, experiment: Experiment | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.payload = payload
        self.experiment = experiment
        self.setWindowTitle("Результат оценки")
        self.resize(1350, 820)
        layout = QVBoxLayout(self)
        integral = payload.get("integral", {})
        total = integral.get("value")
        state = "сохранён" if saved else "рассчитан, но не сохранён"
        context_lines = [
            f"Опыт: {payload.get('experiment_id', '')} · Ω: {payload.get('profile_id', '')} "
            f"v{payload.get('profile_version', '—')} · вход v{payload.get('input_revision', '—')} · "
            f"модель: {payload.get('model_version', '')}"
        ]
        if experiment is not None:
            passport = experiment.passport
            algorithm = passport.get("algorithm", {})
            environment = passport.get("environment", {})
            context_lines.append(
                f"Алгоритм: {algorithm.get('name_ru', algorithm.get('algorithm_id', '—'))} "
                f"{algorithm.get('implementation_version', '')} · сеть: "
                f"{passport.get('network', {}).get('network_id', '—')} · "
                f"сценарий: {passport.get('scenario_id', '—')} · повтор: {passport.get('repeat_id', '—')} · "
                f"источник: {environment.get('execution_source', '—')}"
            )
        context_lines.append(
            f"Итог: {_display_number(total) if total is not None else integral.get('status', 'неизвестно')} баллов из 100 · {state}"
        )
        context_label = QLabel("\n".join(context_lines))
        if total is not None:
            context_label.setToolTip(f"Полная десятичная точность I(A): {total}")
        layout.addWidget(context_label)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        table = QTableWidget(0, 10)
        table.setHorizontalHeaderLabels(["Показатель", "Исходное значение", "Единица", "Направление",
                                         "Статус", "Нормализация", "Bᵢ(A)", "wᵢ", "Kᵢ(A)", "Пояснение"])
        table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((155, 145, 65, 85, 90, 120, 120, 65, 120, 200)):
            table.setColumnWidth(column, width)
        for key, metric in payload.get("metrics", {}).items():
            row = table.rowCount()
            table.insertRow(row)
            raw = metric.get("raw", {})
            normalized = metric.get("normalized", {})
            if raw.get("value") is not None:
                raw_value = _display_number(raw["value"])
            elif raw.get("status") == "interval":
                lower = _display_number(raw.get("lower"))
                upper = _display_number(raw.get("upper")) if raw.get("upper") is not None else "∞"
                left = "(" if raw.get("lower_open") else "["
                right = ")" if raw.get("upper_open") else "]"
                raw_value = f"{left}{lower}, {upper}{right}"
            else:
                raw_value = "—"
            values = (
                f"{metric.get('symbol', key)} ({key})",
                raw_value,
                raw.get("unit", ""),
                metric.get("direction", ""),
                raw.get("status", ""),
                _display_number(normalized.get("value")) if normalized.get("value") is not None else normalized.get("status", ""),
                _display_number(metric.get("partial_score", {}).get("value"))
                if metric.get("partial_score", {}).get("value") is not None else metric.get("partial_score", {}).get("status", ""),
                _display_number(metric.get("weight", "")),
                _display_number(metric.get("contribution", {}).get("value"))
                if metric.get("contribution", {}).get("value") is not None else metric.get("contribution", {}).get("status", ""),
                raw.get("reason") or normalized.get("reason") or "",
            )
            detail = json.dumps(metric, ensure_ascii=False, indent=2)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(detail)
                table.setItem(row, column, item)
        summary_layout.addWidget(table)
        summary_layout.addWidget(QLabel(
            "Полные десятичные значения доступны в подсказках строк и при экспорте JSON/CSV."
        ))
        charts = QHBoxLayout()
        self.radar_chart = RadarChart(payload)
        self.contribution_chart = ContributionChart(payload)
        charts.addWidget(self.radar_chart)
        charts.addWidget(self.contribution_chart)
        summary_layout.addLayout(charts)
        groups = payload.get("groups", {})
        group_line = "Группы: " + "; ".join(
            f"{key} = {_display_number(value.get('value')) if value.get('value') is not None else value.get('status')}"
            for key, value in groups.items()
        )
        summary_layout.addWidget(QLabel(group_line))
        warnings = payload.get("warnings", [])
        if warnings:
            summary_layout.addWidget(QLabel("Предупреждения:\n" + "\n".join(warnings)))
        chart_exports = QHBoxLayout()
        for label, chart, title, filename in (
            ("Радар PNG", self.radar_chart, "Сохранить радар", "radar.png"),
            ("Вклады PNG", self.contribution_chart, "Сохранить вклады", "contributions.png"),
        ):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, w=chart, t=title, f=filename:
                                   self.export_png(w, t, f))
            chart_exports.addWidget(button)
        summary_layout.addLayout(chart_exports)
        tabs.addTab(summary, "Таблица и радар")

        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        compare_layout.addWidget(QLabel(
            "Выберите серию и две сохранённые оценки. Перед сравнением программа проверит сеть, заданный трафик, "
            "внешние события, окно наблюдений, проверки, шкалы и веса. Фактические маршруты и потери "
            "сами по себе сравнение не запрещают."
        ))
        compare_button = QPushButton("Выбрать серию для сравнения")
        compare_callback = getattr(parent, "manage_series", None)
        compare_button.setEnabled(callable(compare_callback))
        if callable(compare_callback):
            compare_button.clicked.connect(compare_callback)
        compare_layout.addWidget(compare_button)
        compare_layout.addStretch(1)
        tabs.addTab(compare_page, "Сравнение серии")

        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        filters = QHBoxLayout()
        self.flow_filter = QComboBox()
        self.flow_filter.addItem("Все потоки", None)
        self.link_filter = QComboBox()
        self.link_filter.addItem("Все каналы", None)
        self.event_zoom = QComboBox()
        self.event_zoom.addItem("Весь интервал", None)
        for flow_id in (experiment.passport.get("traffic", {}).get("flows", []) if experiment else []):
            self.flow_filter.addItem(str(flow_id.get("flow_id", "")), flow_id.get("flow_id"))
        for link in (experiment.passport.get("network", {}).get("links", []) if experiment else []):
            self.link_filter.addItem(str(link.get("link_id", "")), link.get("link_id"))
        events = experiment.passport.get("scenario", {}).get("events", []) if experiment else []
        for item in events:
            actual = item.get("actual_time_s")
            if item.get("result") == "executed" and actual is not None:
                self.event_zoom.addItem(f"Увеличить: {item.get('event_type')} · {actual} s", float(actual))
        for label, combo in (("Поток", self.flow_filter), ("Канал", self.link_filter), ("Участок", self.event_zoom)):
            filters.addWidget(QLabel(label))
            filters.addWidget(combo)
        timeline_layout.addLayout(filters)
        self.timeline_chart = TimelineChart(experiment) if experiment is not None else None
        if self.timeline_chart is not None:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(self.timeline_chart)
            timeline_layout.addWidget(scroll)
            self.flow_filter.currentIndexChanged.connect(self.update_timeline)
            self.link_filter.currentIndexChanged.connect(self.update_timeline)
            self.event_zoom.currentIndexChanged.connect(self.update_timeline)
            timeline_export = QPushButton("Сохранить диаграмму Ганта в PNG")
            timeline_export.clicked.connect(lambda: self.export_png(
                self.timeline_chart, "Сохранить диаграмму Ганта", "timeline.png"))
            timeline_layout.addWidget(timeline_export)
        else:
            timeline_layout.addWidget(QLabel("Исходные наблюдения недоступны."))
        tabs.addTab(timeline_page, "Диаграмма Ганта")

        explanation = QPlainTextEdit()
        explanation.setReadOnly(True)
        explanation.setPlainText(json.dumps({
            "formula_sources": {
                "P_zad": "(3.14)–(3.15)", "P_pr": "(3.16)", "P_pot": "(3.17)–(3.18)",
                "P_bal": "(3.19)–(3.23)", "P_ust": "(3.24)–(3.26)",
                "P_ad": "(3.27)–(3.30)", "P_res": "(3.31)",
                "normalization_and_weights": "(3.33)–(3.38)", "integral": "(3.39)–(3.45)",
            },
            "metrics": {key: {"raw": value.get("raw"), "trace": value.get("trace")}
                        for key, value in payload.get("metrics", {}).items()},
            "warnings": warnings,
        }, ensure_ascii=False, indent=2))
        tabs.addTab(explanation, "Пояснения")
        exports = QHBoxLayout()
        if saved and callable(getattr(parent, "assign_assessment_to_series", None)):
            series_button = QPushButton("Добавить в серию")
            series_button.clicked.connect(lambda: parent.assign_assessment_to_series(
                self.payload.get("assessment_id")))
            exports.addWidget(series_button)
        for label, callback in (("Сохранить JSON", self.export_json),
                                ("Сохранить CSV", self.export_csv)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            exports.addWidget(button)
        layout.addLayout(exports)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def update_timeline(self) -> None:
        if self.timeline_chart is None or self.experiment is None:
            return
        start = float(self.timeline_chart.context.capture_start)
        end = float(self.timeline_chart.context.capture_end)
        event_time = self.event_zoom.currentData()
        if event_time is not None:
            span = max((end - start) * 0.2, 1e-6)
            start, end = max(start, event_time - span / 2), min(end, event_time + span / 2)
        self.timeline_chart.set_view(self.flow_filter.currentData(), self.link_filter.currentData(), (start, end))

    def export_png(self, widget: QWidget, title: str, filename: str) -> None:
        path, _ = QFileDialog.getSaveFileName(self, title, filename, "PNG (*.png)")
        if not path:
            return
        if not widget.grab().save(path, "PNG"):
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось сохранить PNG: {path}")

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт результата JSON", "assessment.json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                                  encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            QMessageBox.critical(self, "Ошибка экспорта", str(exc))

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт показателей CSV", "metrics.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["metric", "symbol", "direction", "raw_value", "raw_lower", "raw_upper",
                                 "lower_open", "upper_open", "upper_unbounded", "unit", "status", "reason",
                                 "normalized", "partial_score", "weight", "contribution"])
                for key, metric in self.payload.get("metrics", {}).items():
                    raw = metric.get("raw", {})
                    writer.writerow([
                        key, metric.get("symbol", ""), metric.get("direction", ""), raw.get("value"),
                        raw.get("lower"), raw.get("upper"), raw.get("lower_open"), raw.get("upper_open"),
                        raw.get("upper_unbounded"), raw.get("unit"), raw.get("status"),
                        raw.get("reason") or metric.get("normalized", {}).get("reason"),
                        metric.get("normalized", {}).get("value"),
                        metric.get("partial_score", {}).get("value"), metric.get("weight"),
                        metric.get("contribution", {}).get("value"),
                    ])
        except OSError as exc:
            QMessageBox.critical(self, "Ошибка экспорта", str(exc))


class ComparisonDialog(QDialog):
    def __init__(self, first: dict[str, Any], second: dict[str, Any], reasons: list[str],
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение сохранённых оценок")
        self.resize(1000, 500)
        layout = QVBoxLayout(self)
        if reasons:
            layout.addWidget(QLabel(
                "Условия несопоставимы. Общую оценку и рейтинг не формируем:\n• " + "\n• ".join(reasons)
            ))
        else:
            layout.addWidget(QLabel("Условия сопоставимы. Показаны сохранённые оценки без повторного расчёта."))
        layout.addWidget(QLabel(
            f"{first.get('experiment_id')} — I(A): {self._show_value(first.get('integral', {}))}\n"
            f"{second.get('experiment_id')} — I(A): {self._show_value(second.get('integral', {}))}"
        ))
        metrics = list(first.get("metrics", {}))
        table = QTableWidget(len(metrics), 7)
        table.setHorizontalHeaderLabels(["Показатель", "B₁", "B₂", "K₁", "K₂", "Статус 1", "Статус 2"])
        table.horizontalHeader().setStretchLastSection(True)
        for row, key in enumerate(metrics):
            a = first["metrics"][key]
            b = second.get("metrics", {}).get(key, {})
            values = [
                f"{a.get('symbol', key)} ({key})",
                self._show_value(a.get("partial_score", {})),
                self._show_value(b.get("partial_score", {})),
                self._show_value(a.get("contribution", {})),
                self._show_value(b.get("contribution", {})),
                a.get("raw", {}).get("status"), b.get("raw", {}).get("status"),
            ]
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value or "—")))
        layout.addWidget(table)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    @staticmethod
    def _show_value(value: dict[str, Any]) -> str:
        if value.get("status") == "exact":
            return str(value.get("value"))
        if value.get("status") == "interval":
            left = "(" if value.get("lower_open") else "["
            right = ")" if value.get("upper_open") else "]"
            upper = value.get("upper") if value.get("upper") is not None else "∞"
            return f"{left}{value.get('lower')}, {upper}{right}"
        return str(value.get("status", "unknown"))


class SeriesManagerDialog(QDialog):
    def __init__(self, store: DraftStore, parent: QWidget | None = None,
                 assessment_id: str | None = None):
        super().__init__(parent)
        self.store = store
        self.parent_window = parent
        self.setWindowTitle("Серии экспериментов")
        self.resize(720, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Серия объединяет сохранённые оценки. Закрытие серии не удаляет записи и не пересчитывает их."))
        layout.addWidget(QLabel("Серия:"))
        self.series_combo = QComboBox()
        layout.addWidget(self.series_combo)
        layout.addWidget(QLabel("Сохранённая оценка для добавления:"))
        self.assessment_combo = QComboBox()
        layout.addWidget(self.assessment_combo)
        rows = QHBoxLayout()
        for label, callback in (("Создать серию", self.create_series),
                                ("Добавить оценку", self.add_assessment),
                                ("Продолжить серию", self.activate_series),
                                ("Сравнить оценки", self.compare_series),
                                ("Завершить серию", self.close_series)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            rows.addWidget(button)
        layout.addLayout(rows)
        done = QPushButton("Закрыть")
        done.clicked.connect(self.accept)
        layout.addWidget(done)
        self.preselected_assessment = assessment_id
        self.refresh()

    def refresh(self) -> None:
        self.series_combo.clear()
        active = getattr(self.parent_window, "active_series_id", None)
        for series_id, title, status, count in self.store.list_series():
            label = f"{title} — {status}, опытов: {count}"
            self.series_combo.addItem(label, series_id)
            if series_id == active:
                self.series_combo.setCurrentIndex(self.series_combo.count() - 1)
        self.assessment_combo.clear()
        for assessment_id, draft_id, revision, model, stamp in self.store.list_assessments():
            self.assessment_combo.addItem(
                f"{draft_id}, вход v{revision}, {model}, {stamp}", assessment_id,
            )
            if assessment_id == self.preselected_assessment:
                self.assessment_combo.setCurrentIndex(self.assessment_combo.count() - 1)

    def create_series(self) -> None:
        title, ok = QInputDialog.getText(self, "Новая серия", "Название серии:")
        if not ok:
            return
        try:
            series_id = self.store.create_series(title)
        except (OSError, sqlite3.Error, ValueError) as exc:
            QMessageBox.critical(self, "Ошибка создания серии", str(exc))
            return
        self.refresh()
        index = self.series_combo.findData(series_id)
        self.series_combo.setCurrentIndex(index)
        self.activate_series()

    def add_assessment(self) -> None:
        series_id = self.series_combo.currentData()
        assessment_id = self.assessment_combo.currentData()
        if series_id is None or assessment_id is None:
            QMessageBox.information(self, "Добавление оценки", "Выберите серию и сохранённую оценку")
            return
        try:
            position = self.store.add_to_series(series_id, assessment_id)
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            QMessageBox.critical(self, "Ошибка добавления", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "Серия", f"Оценка находится на позиции {position}")

    def activate_series(self) -> None:
        series_id = self.series_combo.currentData()
        if series_id is None:
            QMessageBox.information(self, "Серия", "Сначала создайте или выберите серию")
            return
        series = next((item for item in self.store.list_series() if item[0] == series_id), None)
        if series is None or series[2] != "open":
            QMessageBox.information(self, "Серия", "Завершённую серию продолжить нельзя")
            return
        if self.parent_window is not None:
            self.parent_window.active_series_id = series_id
            self.parent_window.statusBar().showMessage(f"Активна серия: {series[1]}")
        self.accept()

    def close_series(self) -> None:
        series_id = self.series_combo.currentData()
        if series_id is None:
            return
        answer = QMessageBox.question(
            self, "Завершить серию?", "Серия будет закрыта; её оценки останутся доступными.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.close_series(series_id)
        except (KeyError, OSError, sqlite3.Error) as exc:
            QMessageBox.critical(self, "Ошибка завершения", str(exc))
            return
        if self.parent_window is not None and self.parent_window.active_series_id == series_id:
            self.parent_window.active_series_id = None
        self.refresh()

    def compare_series(self) -> None:
        series_id = self.series_combo.currentData()
        if series_id is None:
            return
        callback = getattr(self.parent_window, "compare_series", None)
        if callable(callback):
            callback(series_id)


def _value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


ARRAY_TEMPLATES: dict[str, Any] = {
    "network.nodes": {"node_id": "", "node_type": "", "plane_id": None, "initial_up": None},
    "network.links": {"link_id": "", "node_from": "", "node_to": "", "interface_from": "",
                      "interface_to": "", "link_type": "", "initial_up": None,
                      "initial_capacity_bps": None, "initial_propagation_delay_s": None,
                      "initial_routing_cost": None, "resource_allocation": ""},
    "traffic.flows": {"flow_id": "", "source_node": "", "destination_node": "", "transport": "",
                      "packetization": {"payload_bytes": None, "network_header_bytes": None,
                                        "fragmentation": None, "transport_retransmission": None,
                                        "duplication": None},
                      "generation": {"type": "", "start_s": None, "interval_s": None,
                                     "packet_count": None, "stop_s": None,
                                     "nominal_payload_rate_bps": None, "first_packet_index": None}},
    "scenario.events": {"event_id": "", "event_type": "", "object_type": "",
                        "object_ids": [], "scheduled_time_s": None, "actual_time_s": None,
                        "result": "", "changes": {}},
    "measurement.stability.checks": {"check_id": "", "flow_id": "", "time_start_s": None, "horizon_s": None},
    "measurement.adaptivity.checks": {"check_id": "", "flow_id": "", "event_id": ""},
}


class JsonTree(QWidget):
    changed = Signal()

    def __init__(self, value: Any, title: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title + " — двойной щелчок изменяет значение или тип"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Поле", "Тип", "Значение"])
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 100)
        self.tree.header().setStretchLastSection(True)
        layout.addWidget(self.tree)
        buttons = QHBoxLayout()
        for label, action in (("Добавить поле/элемент", self.add_child), ("Удалить выбранное", self.remove_selected)):
            button = QPushButton(label)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.tree.itemChanged.connect(lambda *_: self.changed.emit())
        self.set_value(value)

    def _item(self, name: str, value: Any) -> QTreeWidgetItem:
        kind = _value_type(value)
        text = "" if kind in {"object", "array", "null"} else ("true" if value is True else "false" if value is False else str(value))
        item = QTreeWidgetItem([name, kind, text])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if kind == "object":
            for key, child in value.items():
                item.addChild(self._item(str(key), child))
        elif kind == "array":
            for index, child in enumerate(value):
                item.addChild(self._item(str(index), child))
        return item

    def set_value(self, value: Any) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree.addTopLevelItem(self._item("корень", value))
        self.tree.expandToDepth(1)
        self.tree.blockSignals(False)

    def _read(self, item: QTreeWidgetItem) -> Any:
        kind = item.text(1).strip()
        if kind == "object":
            result = {}
            for i in range(item.childCount()):
                child = item.child(i)
                key = child.text(0).strip()
                if not key or key in result:
                    raise ValueError(f"Пустое или повторное имя поля: {key!r}")
                result[key] = self._read(child)
            return result
        if kind == "array":
            return [self._read(item.child(i)) for i in range(item.childCount())]
        if kind == "null":
            return None
        text = item.text(2).strip()
        if kind == "boolean":
            if text.lower() not in {"true", "false"}:
                raise ValueError("Для boolean укажите true или false")
            return text.lower() == "true"
        if kind == "integer":
            return int(text)
        if kind == "number":
            result = float(text)
            if not __import__("math").isfinite(result):
                raise ValueError("Нужно конечное число")
            return result
        if kind == "string":
            return item.text(2)
        raise ValueError(f"Неизвестный тип {kind!r}; используйте object/array/string/integer/number/boolean/null")

    def value(self) -> Any:
        return self._read(self.tree.topLevelItem(0))

    def add_child(self) -> None:
        item = self.tree.currentItem() or self.tree.topLevelItem(0)
        if item.text(1) not in {"object", "array"}:
            item = item.parent() or item
        if item.text(1) not in {"object", "array"}:
            return
        path_parts = []
        current = item
        while current.parent() is not None:
            path_parts.append(current.text(0))
            current = current.parent()
        path = ".".join(reversed(path_parts))
        if item.text(1) == "object":
            name, ok = QInputDialog.getText(self, "Новое поле", "Имя поля:")
            if not ok or not name.strip():
                return
        else:
            name = str(item.childCount())
        if path in ARRAY_TEMPLATES:
            child = self._item(name, ARRAY_TEMPLATES[path])
            item.addChild(child)
            item.setExpanded(True)
            self.tree.setCurrentItem(child)
            self.changed.emit()
            return
        if path == "metrics" and name in METRICS:
            child = self._item(name, {"direction": DIRECTIONS[name], "weight": None, "points": []})
            item.addChild(child)
            item.setExpanded(True)
            self.tree.setCurrentItem(child)
            self.changed.emit()
            return
        if path.startswith("metrics.") and path.endswith(".points"):
            child = self._item(name, [None, None])
            item.addChild(child)
            item.setExpanded(True)
            self.tree.setCurrentItem(child)
            self.changed.emit()
            return
        kind, ok = QInputDialog.getItem(self, "Тип значения", "Тип:",
                                        ["object", "array", "string", "integer", "number", "boolean", "null"], 0, False)
        if not ok:
            return
        child = QTreeWidgetItem([name, kind, "false" if kind == "boolean" else "0" if kind in {"integer", "number"} else ""])
        child.setFlags(child.flags() | Qt.ItemFlag.ItemIsEditable)
        item.addChild(child)
        item.setExpanded(True)
        self.tree.setCurrentItem(child)
        self.changed.emit()

    def remove_selected(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.parent() is None:
            return
        item.parent().removeChild(item)
        self.changed.emit()


class CsvModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.header = HEADERS[name]
        self.rows: list[dict[str, str]] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.header)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self.rows[index.row()][self.header[index.column()]]
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role == Qt.ItemDataRole.DisplayRole:
            return self.header[section] if orientation == Qt.Orientation.Horizontal else str(section + 2)
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        return super().flags(index) | Qt.ItemFlag.ItemIsEditable if index.isValid() else super().flags(index)

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        self.rows[index.row()][self.header[index.column()]] = str(value)
        self.dataChanged.emit(index, index)
        self.changed.emit()
        return True

    def set_rows(self, rows: list[dict[str, str]]) -> None:
        self.beginResetModel()
        self.rows = [dict(row) for row in rows]
        self.endResetModel()

    def insertRows(self, row: int, count: int, parent: QModelIndex = QModelIndex()) -> bool:
        self.beginInsertRows(QModelIndex(), row, row + count - 1)
        for _ in range(count):
            self.rows.insert(row, {name: "" for name in self.header})
        self.endInsertRows()
        self.changed.emit()
        return True

    def removeRows(self, row: int, count: int, parent: QModelIndex = QModelIndex()) -> bool:
        self.beginRemoveRows(QModelIndex(), row, row + count - 1)
        del self.rows[row:row + count]
        self.endRemoveRows()
        self.changed.emit()
        return True


class TableEditor(QWidget):
    changed = Signal()

    def __init__(self, name: str):
        super().__init__()
        self.model = CsvModel(name)
        self.model.changed.connect(self.changed)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setColumnHidden(0, True)  # experiment_id comes from the passport.
        self.view.setAlternatingRowColors(True)
        self.view.horizontalHeader().setStretchLastSection(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)
        buttons = QHBoxLayout()
        for label, callback in (("Добавить строку", self.add_row), ("Удалить строки", self.delete_rows),
                                ("Копировать", self.copy), ("Вставить диапазон", self.paste)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def add_row(self) -> None:
        selected = self.view.currentIndex()
        row = selected.row() + 1 if selected.isValid() else self.model.rowCount()
        self.model.insertRows(row, 1)
        self.view.setCurrentIndex(self.model.index(row, 1))

    def delete_rows(self) -> None:
        rows = sorted({idx.row() for idx in self.view.selectedIndexes()}, reverse=True)
        for row in rows:
            self.model.removeRows(row, 1)

    def copy(self) -> None:
        selection = self.view.selectedIndexes()
        if not selection:
            return
        rows = sorted({i.row() for i in selection})
        cols = sorted({i.column() for i in selection})
        text = "\n".join("\t".join(self.model.rows[row][self.model.header[col]] for col in cols) for row in rows)
        QApplication.clipboard().setText(text)

    def paste(self) -> None:
        text = QApplication.clipboard().text().rstrip("\r\n")
        if not text:
            return
        grid = [line.split("\t") for line in text.splitlines()]
        start = self.view.currentIndex()
        row0 = start.row() if start.isValid() else self.model.rowCount()
        col0 = start.column() if start.isValid() else 0
        needed = row0 + len(grid) - self.model.rowCount()
        if needed > 0:
            self.model.insertRows(self.model.rowCount(), needed)
        for r, values in enumerate(grid):
            for col, value in enumerate(values, col0):
                if col < self.model.columnCount():
                    self.model.setData(self.model.index(row0 + r, col), value)


def empty_passport() -> dict[str, Any]:
    """A blank structure; it does not fabricate packet, route or channel observations."""
    return {
        "format_name": "PAK_ROUTING_EXPORT_4F", "schema_version": "1.0.0",
        "data_origin": "manual_prepared", "description_ru": "", "experiment_id": "",
        "scenario_id": "", "repeat_id": None,
        "algorithm": {"algorithm_id": "", "name_ru": "", "implementation_version": "",
                      "routing_mode": "single_path_per_flow", "implementation_sha256": None,
                      "settings": {}, "settings_completeness": "", "note_ru": ""},
        "environment": {"execution_source": "", "pak_version": None, "time_mode": "",
                        "hardware_description": None, "queue_discipline": "",
                        "buffer_limit_bytes": None, "link_errors": {}, "randomness": {"used": False, "seeds": {}}},
        "network": {"network_id": "", "directed": True, "nodes": [], "links": [], "orbital_description": None},
        "traffic": {"flows": []}, "scenario": {"events": []},
        "measurement": {"capture_start_s": None, "capture_end_s": None,
                        "evaluation_start_s": None, "evaluation_end_s": None,
                        "packet_cohort": "", "delay_definition": "", "loss_timeout_s": None,
                        "goodput_definition": "", "time_base": "", "time_origin": "",
                        "timestamp_resolution_s": None, "clock_error_bound_s": None,
                        "interval_convention": "[start,end)", "packet_tx_point": "", "packet_rx_point": "",
                        "route_observation_mode": "", "route_time_error_bound_s": None,
                        "route_semantics": "", "channel_nominal_interval_s": None,
                        "channel_grid_rule": "", "channel_counter_mode": "interval_delta",
                        "channel_counter_point": "", "channel_byte_scope": "",
                        "capacity_scope": "", "channel_categories_disjoint": True,
                        "channel_max_atomic_packet_bytes": None, "capacity_check_rule": "",
                        "routing_traffic_scope": "", "auxiliary_measurements": {},
                        "balance": {"selected_link_ids": []}, "stability": {"checks": []},
                        "adaptivity": {"horizon_s": None, "confirmation_window_s": None, "checks": []}},
        "file_contract": file_contract(),
        "export_status": {"complete": False, "data_logging_complete": False,
                          "lost_log_records": 0, "logging_gaps": [], "tables": {}},
        "extensions": {},
    }


class MainWindow(QMainWindow):
    def __init__(self, store_path: Path):
        super().__init__()
        self.store = DraftStore(store_path)
        self.draft_id: str | None = None
        self.dirty = False
        self.source = "manual"
        self.original_files: dict[str, bytes] = {}
        self.unsaved_assessment: ResultPersistenceError | None = None
        self.active_series_id: str | None = None
        self.retry_button: QPushButton | None = None
        self.assessment_worker: AssessmentWorker | None = None
        self.cancel_requested: Event | None = None
        self.progress_dialog: QProgressDialog | None = None
        self.running_experiment: Experiment | None = None
        self.setWindowTitle("OPD — подготовка и оценка опыта")
        self.resize(1300, 850)
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        self.pages = QStackedWidget()
        root_layout.addWidget(self.pages)
        self.start_page = self._navigation_page(
            "OPD", "Комплексная оценка алгоритмов маршрутизации", (("Начать", self.show_main_menu),),
        )
        self.main_menu_page = self._navigation_page(
            "Главное меню", "Выберите раздел", (
                ("Эксперименты", self.show_experiments),
                ("Справка", self.show_help), ("О программе", self.show_about), ("Выход", self.close),
            ),
        )
        self.experiments_page = self._navigation_page(
            "Эксперименты", "Подготовка и просмотр оценок", (
                ("Начать новый эксперимент", self.show_input_choices),
                ("Продолжить предыдущий", self.open_draft),
                ("Просмотреть результаты", self.show_saved_results),
                ("Назад в главное меню", self.show_main_menu),
            ),
        )
        self.input_page = self._navigation_page(
            "Новый опыт", "Выберите способ ввода исходных наблюдений", (
                ("Загрузить четыре файла", self.import_folder),
                ("Открыть пустые формы", self.new),
                ("Назад", self.show_experiments),
            ),
        )
        self.saved_results = QComboBox()
        self.results_page = self._navigation_page(
            "Сохранённые результаты", "Оценки открываются без повторного расчёта", (
                ("Открыть выбранную оценку", self.open_selected_result),
                ("Продолжить черновик", self.open_draft),
                ("Серии", self.manage_series), ("Назад", self.show_experiments),
            ),
            extra_widget=self.saved_results,
        )
        for page in (self.start_page, self.main_menu_page, self.experiments_page,
                     self.input_page, self.results_page):
            self.pages.addWidget(page)
        self.editor_page = QWidget()
        self.pages.addWidget(self.editor_page)
        layout = QVBoxLayout(self.editor_page)
        for buttons in (
            (("К разделу экспериментов", self.back_to_experiments),
             ("Новый", self.new), ("Импорт 4 файлов", self.import_folder),
             ("Загрузить Ω", self.import_profile), ("Открыть черновик", self.open_draft),
             ("Экспорт 4 файлов", self.export)),
            (("История результатов", self.open_assessment), ("Сравнить результаты", self.compare_assessments),
             ("Серии", self.manage_series), ("Резервная копия", self.backup_store),
             ("Восстановить копию", self.restore_store)),
        ):
            toolbar = QHBoxLayout()
            for label, callback in buttons:
                button = QPushButton(label)
                button.clicked.connect(callback)
                toolbar.addWidget(button)
            layout.addLayout(toolbar)
        self.retry_button = QPushButton("Повторить сохранение результата")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self.retry_assessment_save)
        layout.addWidget(self.retry_button)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.passport = JsonTree(empty_passport(), "Паспорт и условия")
        self.tabs.addTab(self.passport, "Паспорт и условия")
        self.tables = {}
        for name, title in (("packets.csv", "Пакеты"), ("routes.csv", "Маршруты"), ("channels.csv", "Каналы")):
            editor = TableEditor(name)
            self.tables[name] = editor
            self.tabs.addTab(editor, title)
        self.profile = JsonTree({"profile_id": "", "metrics": {}}, "Профиль оценивания Ω")
        self.tabs.addTab(self.profile, "Профиль оценивания")
        self.passport.changed.connect(self.mark_input_dirty)
        self.profile.changed.connect(self.mark_dirty)
        for editor in self.tables.values():
            editor.changed.connect(self.mark_input_dirty)
        editor_actions = QHBoxLayout()
        for label, callback in (("Назад", self.back_to_experiments),
                                ("Сохранить черновик", self.save_draft),
                                ("Проверить данные", self.check),
                                ("Запуск расчёта", self.run_assessment)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            editor_actions.addWidget(button)
        layout.addLayout(editor_actions)
        self.statusBar().showMessage("Новый черновик. Пустые наблюдения не создаются автоматически.")
        self.pages.setCurrentWidget(self.start_page)

    def _navigation_page(self, title: str, subtitle: str,
                         buttons: tuple[tuple[str, Any], ...],
                         extra_widget: QWidget | None = None) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        heading = QLabel(title)
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 30px; font-weight: 600; color: #203040;")
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)
        if extra_widget is not None:
            layout.addWidget(extra_widget)
        for label, callback in buttons:
            button = QPushButton(label)
            button.setMinimumHeight(42)
            button.clicked.connect(callback)
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        return page

    def show_main_menu(self) -> None:
        self.pages.setCurrentWidget(self.main_menu_page)

    def show_experiments(self) -> None:
        self.pages.setCurrentWidget(self.experiments_page)

    def show_input_choices(self) -> None:
        self.pages.setCurrentWidget(self.input_page)

    def show_editor(self) -> None:
        self.pages.setCurrentWidget(self.editor_page)

    def show_help(self) -> None:
        QMessageBox.information(
            self, "Справка",
            "Создайте опыт вручную или загрузите четыре файла PAK_ROUTING_EXPORT_4F 1.0.0. "
            "Задайте профиль Ω, проверьте данные и запустите оценку К0–К2.",
        )

    def show_about(self) -> None:
        QMessageBox.information(self, "О программе", "OPD — настольная программа оценки алгоритмов маршрутизации.")

    def back_to_experiments(self) -> None:
        if self._confirm_leave():
            self.show_experiments()

    def refresh_saved_results(self) -> None:
        self.saved_results.clear()
        try:
            for assessment_id, draft_id, revision, model, stamp in self.store.list_assessments():
                self.saved_results.addItem(
                    f"{draft_id}, вход v{revision}, {model}, {stamp}", assessment_id,
                )
        except sqlite3.Error as exc:
            self._error(exc)

    def show_saved_results(self) -> None:
        self.refresh_saved_results()
        self.pages.setCurrentWidget(self.results_page)

    def open_selected_result(self) -> None:
        assessment_id = self.saved_results.currentData()
        if assessment_id is None:
            QMessageBox.information(self, "Результаты", "Сохранённых оценок пока нет")
            return
        try:
            payload, experiment, _ = self.store.load_assessment(assessment_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        AssessmentDialog(payload, True, experiment, self).exec()

    def mark_dirty(self) -> None:
        self.dirty = True
        self.setWindowModified(True)

    def mark_input_dirty(self) -> None:
        if self.source == "import":
            self.source = "edited_import"
        self.mark_dirty()

    def _confirm_leave(self) -> bool:
        if not self.dirty:
            return True
        choice = QMessageBox.question(self, "Несохранённые изменения", "Сохранить черновик перед уходом?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self.save_draft()
        return True

    def _set_experiment(self, experiment: Experiment, profile: dict[str, Any] | None = None) -> None:
        self.passport.set_value(experiment.passport)
        rows = experiment.table_strings()
        for name, editor in self.tables.items():
            editor.model.set_rows(rows[name])
        self.profile.set_value(profile or {"profile_id": "", "metrics": {}})
        self.source = experiment.source
        self.original_files = dict(experiment.original_files)
        self.dirty = False
        self.setWindowModified(False)

    def _experiment(self) -> tuple[Experiment, dict[str, Any]]:
        passport = self.passport.value()
        profile = self.profile.value()
        if not isinstance(passport, dict) or not isinstance(profile, dict):
            raise ValueError("Паспорт и профиль должны быть объектами")
        tables = {name: [{**row, "experiment_id": str(passport.get("experiment_id") or "")}
                         for row in editor.model.rows] for name, editor in self.tables.items()}
        experiment = from_manual(passport, tables)
        experiment.source = self.source
        experiment.original_files = dict(self.original_files)
        return experiment, profile

    def _error(self, exc: Exception) -> None:
        QMessageBox.critical(self, "Ошибка", str(exc))

    def new(self) -> None:
        if not self._confirm_leave():
            return
        self.draft_id = None
        self._set_experiment(Experiment(empty_passport(), {name: [] for name in HEADERS}))
        self.show_editor()
        self.statusBar().showMessage("Новый черновик")

    def import_folder(self) -> None:
        if not self._confirm_leave():
            return
        folder = QFileDialog.getExistingDirectory(self, "Папка с четырьмя файлами")
        if not folder:
            return
        try:
            experiment = read_folder(folder)
        except (DataError, OSError, ValueError) as exc:
            self._error(exc)
            return
        self.draft_id = None
        self._set_experiment(experiment)
        self.dirty = True  # Import is not yet persisted, but its origin remains "import".
        self.setWindowModified(True)
        self.show_editor()
        self.statusBar().showMessage(f"Импортирован {experiment.experiment_id}; исходные файлы будут сохранены с черновиком")

    def open_draft(self) -> None:
        if not self._confirm_leave():
            return
        entries = self.store.list_drafts()
        if not entries:
            QMessageBox.information(self, "Черновики", "Сохранённых черновиков нет")
            return
        labels = [f"{draft_id} — версия {revision} ({stamp})" for draft_id, revision, stamp in entries]
        label, ok = QInputDialog.getItem(self, "Открыть черновик", "Выберите черновик:", labels, 0, False)
        if not ok:
            return
        index = labels.index(label)
        self.draft_id = entries[index][0]
        try:
            passport, tables, source, originals, profile, revision = self.store.load_raw(self.draft_id)
            experiment = Experiment(passport, tables, originals, source)
            self._set_experiment(experiment, profile)
            profile_version = self.store.profile_version(self.draft_id, revision)
            suffix = f"; Ω {profile_version[0]} v{profile_version[1]}" if profile_version else ""
            self.statusBar().showMessage(f"Открыт {self.draft_id}, версия {revision}{suffix}")
            self.show_editor()
        except (KeyError, OSError, ValueError) as exc:
            self._error(exc)

    def import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить профиль Ω", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as stream:
                profile = json.load(stream)
            if validate_profile(profile):
                raise ValueError("Профиль Ω не проходит проверку: " + str(validate_profile(profile)[0]))
            self.profile.set_value(profile)
            self.mark_dirty()
            self.statusBar().showMessage(f"Загружен профиль {profile['profile_id']}")
        except (OSError, ValueError) as exc:
            self._error(exc)

    def save_draft(self) -> bool:
        try:
            passport = self.passport.value()
            profile = self.profile.value()
            if not isinstance(passport, dict) or not isinstance(profile, dict):
                raise ValueError("Паспорт и профиль должны быть объектами")
            if self.draft_id is None:
                suggested = str(passport.get("experiment_id") or "draft")
                draft_id, ok = QInputDialog.getText(self, "Идентификатор черновика", "Имя черновика:", text=suggested)
                if not ok:
                    return False
                self.draft_id = draft_id
            tables = {name: [{**row, "experiment_id": str(passport.get("experiment_id") or "")}
                             for row in editor.model.rows] for name, editor in self.tables.items()}
            revision = self.store.save_unvalidated(self.draft_id, passport, tables, profile,
                                                   source=self.source, originals=self.original_files)
        except (DataError, OSError, ValueError) as exc:
            self._error(exc)
            return False
        self.dirty = False
        self.setWindowModified(False)
        profile_version = self.store.profile_version(self.draft_id, revision)
        suffix = f"; Ω {profile_version[0]} v{profile_version[1]}" if profile_version else ""
        self.statusBar().showMessage(f"Черновик {self.draft_id}, версия {revision}{suffix} сохранён")
        return True

    def check(self) -> None:
        try:
            experiment, profile = self._experiment()
            report = validate_experiment(experiment, complete=False)
            if profile.get("profile_id") or profile.get("metrics"):
                report += validate_profile(profile)
            errors = [item for item in report if item.severity == "error"]
            message = "\n".join(str(item) for item in report[:30])
            if len(report) > 30:
                message += f"\n… ещё {len(report) - 30} сообщений"
            if errors:
                QMessageBox.warning(self, "Проверка данных", message)
            else:
                QMessageBox.information(self, "Проверка данных", message or "Ошибок не обнаружено")
        except (DataError, ValueError) as exc:
            self._error(exc)

    def export(self) -> None:
        try:
            experiment, _ = self._experiment()
            folder = QFileDialog.getExistingDirectory(self, "Пустая папка для экспорта")
            if not folder:
                return
            export_four(experiment, folder)
            self.statusBar().showMessage(f"Комплект экспортирован в {folder}")
        except (DataError, OSError, ValueError) as exc:
            self._error(exc)

    def run_assessment(self) -> None:
        if self.assessment_worker is not None and self.assessment_worker.isRunning():
            return
        try:
            experiment, profile = self._experiment()
            draft_id = self.draft_id or str(experiment.passport.get("experiment_id") or "").strip()
            if not draft_id:
                draft_id, ok = QInputDialog.getText(self, "Идентификатор опыта", "Имя записи:")
                if not ok:
                    return
            if not draft_id:
                raise ValueError("Нужен непустой идентификатор записи")
            self.running_experiment = experiment
            self.cancel_requested = Event()
            self.progress_dialog = QProgressDialog("Подготовка расчёта…", "Отменить", 0, 0, self)
            self.progress_dialog.setWindowTitle("Расчёт оценки")
            self.progress_dialog.setWindowModality(Qt.WindowModality.NonModal)
            self.progress_dialog.setMinimumDuration(0)
            self.progress_dialog.setAutoClose(False)
            self.progress_dialog.canceled.connect(self.cancel_requested.set)
            self.progress_dialog.show()
            worker = AssessmentWorker(self.store, draft_id, experiment, profile, self.cancel_requested)
            worker.progress.connect(self._assessment_progress)
            worker.succeeded.connect(self._assessment_succeeded)
            worker.failed.connect(self._assessment_failed)
            worker.finished.connect(self._assessment_thread_finished)
            self.assessment_worker = worker
            worker.start()
        except (DataError, DispatchError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

    def _assessment_progress(self, message: str) -> None:
        if self.progress_dialog is not None:
            self.progress_dialog.setLabelText(message)
        self.statusBar().showMessage(message)

    def _assessment_succeeded(self, result: object) -> None:
        assessment_id, revision, payload = result
        experiment = self.running_experiment
        if experiment is None:
            return
        draft_id = self.assessment_worker.draft_id if self.assessment_worker else self.draft_id
        self.draft_id = draft_id
        self.dirty = False
        self.setWindowModified(False)
        self.unsaved_assessment = None
        self.retry_button.setEnabled(False)
        series_status = ""
        if self.active_series_id is not None:
            try:
                position = self.store.add_to_series(self.active_series_id, assessment_id)
                series_status = f"; серия, позиция {position}"
            except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
                self.active_series_id = None
                series_status = f"; в серию не добавлен: {exc}"
        self.statusBar().showMessage(
            f"Расчёт сохранён: {assessment_id}; вход {draft_id}, версия {revision}{series_status}"
        )
        AssessmentDialog(payload, True, experiment, self).exec()

    def _assessment_failed(self, exc: object) -> None:
        if isinstance(exc, CalculationCancelled):
            self.statusBar().showMessage(str(exc) + "; результат не сохранён")
            return
        if isinstance(exc, ResultPersistenceError):
            self.unsaved_assessment = exc
            self.retry_button.setEnabled(True)
            self.statusBar().showMessage("Расчёт выполнен, результат не сохранён")
            answer = QMessageBox.critical(
                self, "Результат не сохранён",
                str(exc) + "\nРасчёт уже выполнен; можно повторить только сохранение.",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Close,
            )
            if answer == QMessageBox.StandardButton.Retry:
                self.retry_assessment_save()
            elif self.running_experiment is not None:
                AssessmentDialog(exc.payload, False, self.running_experiment, self).exec()
            return
        self._error(exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

    def _assessment_thread_finished(self) -> None:
        if self.progress_dialog is not None:
            self.progress_dialog.close()
            self.progress_dialog.deleteLater()
            self.progress_dialog = None
        worker = self.assessment_worker
        self.assessment_worker = None
        self.cancel_requested = None
        self.running_experiment = None
        if worker is not None:
            worker.deleteLater()

    def retry_assessment_save(self) -> None:
        pending = self.unsaved_assessment
        if pending is None:
            return
        try:
            assessment_id = self.store.save_assessment(
                pending.draft_id, pending.revision, pending.profile_id,
                pending.profile_version, pending.model_version, pending.payload,
                pending.assessment_id,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка сохранения", f"Результат пока не сохранён: {exc}")
            return
        self.unsaved_assessment = None
        self.retry_button.setEnabled(False)
        self.statusBar().showMessage(f"Результат сохранён: {assessment_id}")
        _, experiment, _ = self.store.load_assessment(assessment_id)
        AssessmentDialog(pending.payload, True, experiment, self).exec()

    def open_assessment(self) -> None:
        try:
            entries = self.store.list_assessments()
        except sqlite3.Error as exc:
            self._error(exc)
            return
        if not entries:
            QMessageBox.information(self, "История результатов", "Сохранённых результатов пока нет")
            return
        labels = [f"{draft_id}, версия {revision} — {model}, {stamp}"
                  for _, draft_id, revision, model, stamp in entries]
        label, ok = QInputDialog.getItem(self, "История результатов", "Выберите сохранённую оценку:",
                                         labels, 0, False)
        if not ok:
            return
        assessment_id = entries[labels.index(label)][0]
        try:
            payload, experiment, _ = self.store.load_assessment(assessment_id)
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        self.statusBar().showMessage(f"Открыт сохранённый результат {assessment_id}; повторного расчёта не было")
        AssessmentDialog(payload, True, experiment, self).exec()

    def compare_assessments(self) -> None:
        try:
            entries = self.store.list_assessments()
        except sqlite3.Error as exc:
            self._error(exc)
            return
        if len(entries) < 2:
            QMessageBox.information(self, "Сравнение результатов", "Нужно сохранить хотя бы две оценки")
            return
        labels = [f"{draft_id}, v{revision}, {model}, {stamp}, {assessment_id[:8]}"
                  for assessment_id, draft_id, revision, model, stamp in entries]
        first_label, ok = QInputDialog.getItem(self, "Сравнение результатов", "Первая оценка:", labels, 0, False)
        if not ok:
            return
        first_index = labels.index(first_label)
        remaining = [label for index, label in enumerate(labels) if index != first_index]
        second_label, ok = QInputDialog.getItem(self, "Сравнение результатов", "Вторая оценка:", remaining, 0, False)
        if not ok:
            return
        second_index = labels.index(second_label)
        try:
            first, first_experiment, first_profile = self.store.load_assessment(entries[first_index][0])
            second, second_experiment, second_profile = self.store.load_assessment(entries[second_index][0])
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        reasons = comparability_reasons(first_experiment, first_profile, second_experiment, second_profile)
        ComparisonDialog(first, second, reasons, self).exec()

    def compare_series(self, series_id: str) -> None:
        try:
            entries = self.store.series_assessments(series_id)
        except (KeyError, OSError, sqlite3.Error) as exc:
            self._error(exc)
            return
        if len(entries) < 2:
            QMessageBox.information(self, "Сравнение серии", "В серии нужно сохранить хотя бы две оценки")
            return
        labels = [f"{draft_id}, v{revision}, {model}, {stamp}"
                  for _, draft_id, revision, model, stamp in entries]
        first_label, ok = QInputDialog.getItem(self, "Сравнение серии", "Первая оценка:", labels, 0, False)
        if not ok:
            return
        first_index = labels.index(first_label)
        remaining = [label for index, label in enumerate(labels) if index != first_index]
        second_label, ok = QInputDialog.getItem(self, "Сравнение серии", "Вторая оценка:", remaining, 0, False)
        if not ok:
            return
        second_index = labels.index(second_label)
        try:
            first, first_experiment, first_profile = self.store.load_assessment(entries[first_index][0])
            second, second_experiment, second_profile = self.store.load_assessment(entries[second_index][0])
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return
        reasons = comparability_reasons(first_experiment, first_profile, second_experiment, second_profile)
        ComparisonDialog(first, second, reasons, self).exec()

    def manage_series(self) -> None:
        SeriesManagerDialog(self.store, self).exec()

    def assign_assessment_to_series(self, assessment_id: str | None) -> None:
        if assessment_id is None:
            return
        SeriesManagerDialog(self.store, self, assessment_id).exec()

    def backup_store(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Резервная копия хранилища", "opd-backup.sqlite3",
                                              "SQLite (*.sqlite3)")
        if not path:
            return
        try:
            self.store.backup(path)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._error(exc)
            return
        self.statusBar().showMessage(f"Резервная копия записана: {path}")

    def restore_store(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выбрать резервную копию OPD", "", "SQLite (*.sqlite3 *.db)")
        if not path:
            return
        if self.unsaved_assessment is not None:
            choice = QMessageBox.question(
                self, "Несохранённый результат",
                "Сначала сохранить ожидающий результат? Выбор «Нет» отбросит его.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No |
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Yes:
                self.retry_assessment_save()
                if self.unsaved_assessment is not None:
                    return
            else:
                self.unsaved_assessment = None
                self.retry_button.setEnabled(False)
        if not self._confirm_leave():
            return
        accepted = QMessageBox.question(
            self, "Заменить текущее хранилище?",
            "Восстановление заменит локальную историю содержимым выбранной копии.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if accepted != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.restore(path)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._error(exc)
            return
        self.draft_id = None
        self._set_experiment(Experiment(empty_passport(), {name: [] for name in HEADERS}))
        self.statusBar().showMessage("Резервная копия восстановлена; откройте нужную запись из истории")

    def closeEvent(self, event) -> None:
        if self.assessment_worker is not None and self.assessment_worker.isRunning():
            if self.cancel_requested is not None:
                self.cancel_requested.set()
            QMessageBox.information(
                self, "Расчёт выполняется",
                "Запрос отмены отправлен. Закройте окно после завершения текущего этапа расчёта.",
            )
            event.ignore()
            return
        if self.unsaved_assessment is not None:
            choice = QMessageBox.question(
                self, "Несохранённый результат",
                "Результат расчёта ещё не сохранён. Повторить сохранение перед выходом?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if choice == QMessageBox.StandardButton.Save:
                self.retry_assessment_save()
                if self.unsaved_assessment is not None:
                    event.ignore()
                    return
            else:
                self.unsaved_assessment = None
                self.retry_button.setEnabled(False)
        if self._confirm_leave():
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    path = Path.home() / ".opd" / "drafts.sqlite3"
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
