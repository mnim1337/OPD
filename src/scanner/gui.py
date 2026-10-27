"""Qt application for editing inputs and running saved K0–K2 assessments."""

from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import json
import math
import sqlite3
import sys
from threading import Event
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, QRectF, Qt, Signal, QThread
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QScrollArea, QSizePolicy,
    QPushButton, QProgressDialog, QTableView, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
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
METRIC_NAMES = {
    "P_zad": "Задержка", "P_pr": "Полезная скорость", "P_pot": "Потери",
    "P_bal": "Балансировка", "P_ust": "Устойчивость", "P_ad": "Адаптивность",
    "P_res": "Служебный обмен",
}
CHART_COLORS = [QColor("#2374AB"), QColor("#F18F01"), QColor("#C73E1D"),
                QColor("#3B8C6E"), QColor("#8055A2"), QColor("#D65DB1"), QColor("#65737E")]

# Appendix B visual album: dark masthead, pale workspace, white cards and blue actions.
UI_STYLESHEET = """
QMainWindow, QDialog { background: #f1f5fb; color: #203650; }
QWidget#workspacePage { background: #f1f5fb; }
QWidget#startPage { background: #153251; }
QFrame#masthead { background: #142940; border: 0; }
QLabel#brand { color: #ffffff; font-size: 17px; font-weight: 700; }
QLabel#pageCode { color: #a7bfd8; font-size: 11px; }
QLabel#pageTitle { color: #203650; font-size: 29px; font-weight: 700; }
QLabel#pageSubtitle { color: #71879f; font-size: 14px; }
QLabel#heroTitle { color: #ffffff; font-size: 42px; font-weight: 700; }
QLabel#heroSubtitle { color: #c7d8ea; font-size: 17px; }
QLabel#heroEyebrow { color: #9dbbd9; font-size: 12px; font-weight: 700; }
QLabel#cardTitle { color: #203650; font-size: 19px; font-weight: 700; }
QLabel#cardDescription { color: #71879f; font-size: 14px; }
QLabel#scoreValue { color: #2d62c6; font-size: 36px; font-weight: 700; }
QLabel#fieldLabel { color: #71879f; font-size: 12px; }
QLabel#fieldKey { color: #9aacc0; font-size: 10px; }
QLabel#infoNote { background: #e7effb; color: #2d62c6; border-radius: 8px;
                  padding: 13px 16px; font-size: 13px; }
QFrame#contentCard { background: #ffffff; border: 1px solid #d9e4f2; border-radius: 12px; }
QFrame#footerBar { background: #ffffff; border-top: 1px solid #d9e4f2; }
QPushButton { background: #ffffff; color: #203650; border: 1px solid #d9e4f2;
              border-radius: 7px; padding: 8px 16px; font-weight: 600; min-height: 24px; }
QPushButton:hover { border-color: #2d62c6; background: #f3f7ff; }
QPushButton:disabled { color: #9bacc0; background: #f3f6fa; }
QPushButton[variant="primary"] { background: #2d62c6; color: #ffffff; border-color: #2d62c6; }
QPushButton[variant="primary"]:hover { background: #2257b9; }
QTabWidget::pane { border: 0; background: transparent; }
QTabBar::tab { background: #ffffff; color: #71879f; border: 1px solid #d9e4f2;
               padding: 9px 18px; margin-right: 3px; border-radius: 6px; }
QTabBar::tab:selected { background: #2d62c6; color: #ffffff; border-color: #2d62c6; }
QToolButton#utilityMenu { color: #ffffff; background: #244462; border: 1px solid #42617d;
                          border-radius: 6px; padding: 6px 12px; font-weight: 600; }
QToolButton#utilityMenu:hover { background: #315576; }
QLineEdit { background: #ffffff; color: #203650; border: 1px solid #d9e4f2;
            border-radius: 6px; padding: 6px 9px; min-height: 24px; }
QTableView, QTableWidget, QTreeWidget, QComboBox, QPlainTextEdit {
    background: #ffffff; color: #203650; border: 1px solid #d9e4f2; border-radius: 6px;
    selection-background-color: #e6efff; selection-color: #203650;
}
QHeaderView::section { background: #e7effb; color: #617b99; border: 0;
                       border-bottom: 1px solid #d9e4f2; padding: 8px; font-weight: 600; }
QTableView::item:alternate, QTableWidget::item:alternate { background: #f7f9fd; }
QStatusBar { background: #f1f5fb; color: #71879f; }
"""


class FullWidthTabs(QTabWidget):
    """Keep each row of tabs across the available panel width."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.tabBar().setFixedWidth(self.width())


class NetworkMark(QWidget):
    """Orbital illustration for the opening panel in appendix V."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumSize(260, 210)
        self.setMaximumHeight(380)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        width, height = self.width(), self.height()
        center = QPointF(width * .52, height * .52)
        radius = min(width * .35, height * .42)
        glow = QRadialGradient(center, radius * 1.15)
        glow.setColorAt(0, QColor(45, 112, 170, 125))
        glow.setColorAt(.55, QColor(30, 81, 130, 45))
        glow.setColorAt(1, QColor(20, 50, 81, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(center, radius * 1.15, radius * 1.15)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(135, 183, 218, 105), 1.4))
        for angle in (0, 35, 70, 110, 145):
            painter.save()
            painter.translate(center)
            painter.rotate(angle)
            painter.drawEllipse(QRectF(-radius, -radius * .47, radius * 2, radius * .94))
            painter.restore()
        painter.setPen(QPen(QColor(93, 153, 197, 90), 1))
        painter.drawEllipse(center, radius * .68, radius * .68)
        for dx, dy in ((-.8, -.42), (.84, -.3), (-.63, .63), (.6, .62)):
            x, y = center.x() + radius * dx, center.y() + radius * dy
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3680b8"))
            painter.drawRoundedRect(QRectF(x - 9, y - 6, 18, 12), 2, 2)
            painter.setBrush(QColor("#a8d0ed"))
            painter.drawRect(QRectF(x - 24, y - 5, 12, 10))
            painter.drawRect(QRectF(x + 12, y - 5, 12, 10))
            painter.setPen(QPen(QColor("#153251"), 1))
            painter.drawLine(QPointF(x - 11, y), QPointF(x - 9, y))
            painter.drawLine(QPointF(x + 9, y), QPointF(x + 11, y))
        painter.end()


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


RESULT_STATUS_LABELS = {
    "exact": "точное значение", "interval": "интервал",
    "unknown": "не определено", "not_applicable": "неприменимо",
}


def _display_result(value: dict[str, Any]) -> str:
    if value.get("value") is not None:
        return _display_number(value["value"])
    if value.get("status") == "interval":
        lower = _display_number(value.get("lower"))
        upper = _display_number(value.get("upper")) if value.get("upper") is not None else "∞"
        return f"{'(' if value.get('lower_open') else '['}{lower}, {upper}{')' if value.get('upper_open') else ']'}"
    return RESULT_STATUS_LABELS.get(value.get("status"), "не определено")


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
            total_text = f"I(A): {_display_result(self.integral)}"
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
            shown = _display_result(value)
            painter.drawText(QRectF(x + 19, yy, width / 2 - 18, 25), Qt.AlignmentFlag.AlignLeft,
                             f"{METRIC_LABELS[key]}: {shown}")
        painter.end()


class ComparisonContributionChart(QWidget):
    """Compare saved integral contributions without inventing interval midpoints."""

    def __init__(self, assessments: list[dict[str, Any]], parent: QWidget | None = None):
        super().__init__(parent)
        self.assessments = assessments
        self.setMinimumHeight(150)
        self.setMinimumWidth(500)

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), Qt.GlobalColor.white)
        left, right = 150, max(151, self.width() - 35)
        width = right - left
        painter.setPen(QPen(QColor("#273444"), 1))
        painter.drawText(QRectF(left, 4, width, 22), Qt.AlignmentFlag.AlignCenter,
                         "Состав I(A): семь взвешенных вкладов Kᵢ(A), баллы")
        for row, assessment in enumerate(self.assessments[:2]):
            y = 36 + row * 47
            name = str(assessment.get("experiment_id", f"Опыт {row + 1}"))
            painter.drawText(QRectF(4, y, left - 10, 24), Qt.AlignmentFlag.AlignLeft,
                             name[:24])
            lower_sum = upper_sum = 0.0
            has_unknown = False
            for index, key in enumerate(METRIC_LABELS):
                value = assessment.get("metrics", {}).get(key, {}).get("contribution", {})
                low = self._number(value.get("lower"))
                high = self._number(value.get("upper"))
                if low is None and value.get("status") == "exact":
                    low = self._number(value.get("value"))
                    high = low
                if low is None:
                    has_unknown = True
                    continue
                low = max(0.0, low)
                high = max(low, high) if high is not None else 100.0
                start = left + width * min(100, lower_sum) / 100
                end = left + width * min(100, lower_sum + low) / 100
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(CHART_COLORS[index])
                painter.drawRect(QRectF(start, y, max(0, end - start), 22))
                lower_sum += low
                upper_sum += high
            if upper_sum > lower_sum:
                start = left + width * min(100, lower_sum) / 100
                end = left + width * min(100, upper_sum) / 100
                painter.setPen(QPen(QColor("#8055A2"), 1))
                painter.setBrush(QBrush(QColor("#E8DDF0"), Qt.BrushStyle.BDiagPattern))
                painter.drawRect(QRectF(start, y, max(0, end - start), 22))
            integral = assessment.get("integral", {})
            total_text = "I=" + ComparisonDialog._show_value(integral)
            painter.setPen(QPen(QColor("#273444"), 1))
            painter.drawText(QRectF(left, y + 23, width, 18), Qt.AlignmentFlag.AlignLeft,
                             total_text + ("; неизвестные вклады не масштабированы" if has_unknown else ""))
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

    @staticmethod
    def _unobserved_intervals(segments: list[dict[str, Any]], start: float,
                              end: float) -> list[tuple[float, float]]:
        """Find parts of the visible model-time window without a recorded row."""
        gaps = []
        cursor = start
        for segment in sorted(segments, key=lambda row: float(row["time_start_s"])):
            a = max(start, float(segment["time_start_s"]))
            b = min(end, float(segment["time_end_s"]))
            if b <= a:
                continue
            if a > cursor:
                gaps.append((cursor, a))
            cursor = max(cursor, b)
        if cursor < end:
            gaps.append((cursor, end))
        return gaps

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
            for gap_start, gap_end in self._unobserved_intervals(segments, start, end):
                gap_x = left + (gap_start - start) * scale
                gap_width = (gap_end - gap_start) * scale
                painter.setPen(QPen(QColor("#9A8061"), .6))
                painter.setBrush(QBrush(QColor("#F3E9D5"), Qt.BrushStyle.BDiagPattern))
                painter.drawRect(QRectF(gap_x, y + 5, gap_width, lane_h - 10))
                if gap_width >= 92:
                    painter.setPen(QPen(QColor("#57452E"), 1))
                    painter.drawText(QRectF(gap_x, y + 5, gap_width, lane_h - 10),
                                     Qt.AlignmentFlag.AlignCenter, "нет наблюдений")
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


class AssessmentDialog(QWidget):
    def __init__(self, payload: dict[str, Any], saved: bool, experiment: Experiment | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.payload = payload
        self.experiment = experiment
        self.setWindowTitle("Результат оценки")
        self.resize(1350, 820)
        self.setObjectName("workspacePage")
        self.setFont(QFont("Arial", 11))
        self.setStyleSheet(UI_STYLESHEET)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        masthead = QFrame()
        masthead.setObjectName("masthead")
        masthead.setFixedHeight(56)
        masthead_layout = QHBoxLayout(masthead)
        masthead_layout.setContentsMargins(32, 0, 32, 0)
        brand = QLabel("ОЦЕНКА МАРШРУТИЗАЦИИ")
        brand.setObjectName("brand")
        masthead_layout.addWidget(brand)
        masthead_layout.addStretch()
        code = QLabel("П06.1")
        code.setObjectName("pageCode")
        masthead_layout.addWidget(code)
        layout.addWidget(masthead)
        heading = QLabel("Результаты текущего эксперимента")
        heading.setObjectName("pageTitle")
        heading.setContentsMargins(28, 9, 28, 0)
        layout.addWidget(heading)
        subtitle = QLabel()
        subtitle.setObjectName("pageSubtitle")
        subtitle.setContentsMargins(28, 0, 28, 0)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
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
            f"Итог: {_display_result(integral)} баллов из 100 · {state}"
        )
        context_label = QLabel("\n".join(context_lines))
        context_label.setWordWrap(True)
        context_label.setObjectName("cardDescription")
        if total is not None:
            context_label.setToolTip(f"Полная десятичная точность I(A): {total}")
        summary_strip = QHBoxLayout()
        score_card = QFrame()
        score_card.setObjectName("contentCard")
        score_layout = QVBoxLayout(score_card)
        score_layout.setContentsMargins(22, 12, 22, 12)
        score_caption = QLabel("Интегральная оценка I(A)")
        score_caption.setObjectName("cardDescription")
        score_layout.addWidget(score_caption)
        score_text = _display_result(integral)
        if total is not None:
            try:
                score_text = f"{Decimal(str(total)):.4f}".replace(".", ",")
            except (InvalidOperation, ValueError):
                pass
        score_value = QLabel(score_text)
        score_value.setObjectName("scoreValue")
        score_layout.addWidget(score_value)
        score_layout.addWidget(QLabel("из 100 баллов" if total is not None else
                                     "интервал баллов из 100" if integral.get("status") == "interval" else
                                     "значение не определено"))
        summary_strip.addWidget(score_card, 1)
        context_card = QFrame()
        context_card.setObjectName("contentCard")
        context_card_layout = QVBoxLayout(context_card)
        context_card_layout.setContentsMargins(22, 14, 22, 14)
        result_state = QLabel(
            "Результат определён" if integral.get("status") == "exact" else
            "Интервальная оценка" if integral.get("status") == "interval" else
            "Полная оценка не определяется" if integral.get("status") == "not_applicable" else
            "Оценка не определена"
        )
        result_state.setObjectName("cardTitle")
        context_card_layout.addWidget(result_state)
        if experiment is not None:
            algorithm_title = QLabel(
                (experiment.passport.get("algorithm", {}).get("name_ru") or
                 experiment.passport.get("algorithm", {}).get("algorithm_id") or "Алгоритм не указан")
                + f" · повтор {experiment.passport.get('repeat_id') or '—'}"
            )
            algorithm_title.setWordWrap(True)
            context_card_layout.addWidget(algorithm_title)
        context_card_layout.addWidget(context_label)
        summary_strip.addWidget(context_card, 3)
        tabs = FullWidthTabs()
        tabs.tabBar().setExpanding(True)
        tabs_container = QWidget()
        tabs_container_layout = QVBoxLayout(tabs_container)
        tabs_container_layout.setContentsMargins(28, 0, 28, 0)
        tabs_container_layout.addWidget(tabs)
        layout.addWidget(tabs_container, 1)
        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(0, 8, 0, 8)
        summary_layout.addLayout(summary_strip)
        if integral.get("status") in ("interval", "unknown", "not_applicable"):
            causes = [
                str(item.get("raw", {}).get("reason"))
                for item in payload.get("metrics", {}).values()
                if item.get("raw", {}).get("reason")
            ]
            if integral.get("status") == "not_applicable":
                message = "Полный балл не определяется; доступные частные показатели сохранены. "
                message += "Неприменимый показатель не исключается с автоматическим перераспределением веса."
            else:
                message = "Диапазон или неопределённость отражают ограничения данных или правил их обработки; это не доверительный интервал. "
                message += "Неизвестные значения не заменяются нулём."
            if causes:
                message += " Причина: " + "; ".join(dict.fromkeys(causes))
            state_note = QLabel(message)
            state_note.setObjectName("infoNote")
            state_note.setWordWrap(True)
            summary_layout.addWidget(state_note)
        overview = QHBoxLayout()
        overview.setSpacing(14)
        table_panel = QFrame()
        table_panel.setObjectName("contentCard")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(14, 14, 14, 14)
        table_heading = QLabel("Показатели и взвешенные вклады")
        table_heading.setObjectName("cardTitle")
        table_layout.addWidget(table_heading)
        table = QTableWidget(0, 10)
        table.setObjectName("resultTable")
        table.setHorizontalHeaderLabels(["Показатель", "Исходное значение", "Единица", "Направление",
                                         "Статус", "Нормализация", "Bᵢ(A)", "wᵢ", "Kᵢ(A)", "Пояснение"])
        table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((190, 145, 70, 85, 90, 120, 100, 65, 100, 200)):
            table.setColumnWidth(column, width)
        table.verticalHeader().hide()
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        metrics = payload.get("metrics", {})
        metric_order = [key for key in METRIC_LABELS if key in metrics]
        metric_order.extend(key for key in metrics if key not in METRIC_LABELS)
        for key in metric_order:
            metric = metrics[key]
            row = table.rowCount()
            table.insertRow(row)
            raw = metric.get("raw", {})
            normalized = metric.get("normalized", {})
            raw_value = _display_result(raw)
            values = (
                f"{METRIC_NAMES.get(key, key)} · {metric.get('symbol', key)}",
                raw_value,
                raw.get("unit", ""),
                {"min": "меньше", "max": "больше"}.get(metric.get("direction"), metric.get("direction", "")),
                RESULT_STATUS_LABELS.get(raw.get("status"), raw.get("status", "")),
                _display_result(normalized),
                _display_result(metric.get("partial_score", {})),
                _display_number(metric.get("weight", "")),
                _display_result(metric.get("contribution", {})),
                raw.get("reason") or normalized.get("reason") or "",
            )
            detail = json.dumps(metric, ensure_ascii=False, indent=2)
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(detail)
                table.setItem(row, column, item)
        detail_columns = (3, 4, 5, 9)
        for column in detail_columns:
            table.setColumnHidden(column, True)
        show_details = QPushButton("Показать статус, нормализацию и пояснение")
        def toggle_details() -> None:
            expanded = table.isColumnHidden(detail_columns[0])
            for column in detail_columns:
                table.setColumnHidden(column, not expanded)
            show_details.setText("Скрыть дополнительные поля" if expanded else
                                 "Показать статус, нормализацию и пояснение")
        show_details.clicked.connect(toggle_details)
        table_layout.addWidget(table, 1)
        table_layout.addWidget(show_details)
        table_layout.addWidget(QLabel(
            "Полные десятичные значения доступны в подсказках строк и при экспорте JSON/CSV."
        ))
        overview.addWidget(table_panel, 3)
        self.radar_chart = RadarChart(payload)
        radar_panel = QFrame()
        radar_panel.setObjectName("contentCard")
        radar_layout = QVBoxLayout(radar_panel)
        radar_layout.setContentsMargins(10, 14, 10, 14)
        radar_heading = QLabel("Радар частных баллов")
        radar_heading.setObjectName("cardTitle")
        radar_layout.addWidget(radar_heading)
        radar_layout.addWidget(self.radar_chart, 1)
        radar_export = QPushButton("Сохранить радар PNG")
        radar_export.clicked.connect(lambda: self.export_png(self.radar_chart, "Сохранить радар", "radar.png"))
        radar_layout.addWidget(radar_export)
        overview.addWidget(radar_panel, 2)
        summary_layout.addLayout(overview, 1)
        self.contribution_chart = ContributionChart(payload)
        groups = payload.get("groups", {})
        group_names = {"I_1": "качество", "I_2": "ресурсы",
                       "I_3": "устойчивость/адаптивность", "I_4": "служебный обмен"}
        group_line = "Группы: " + "; ".join(
            f"{group_names.get(key, key)} {_display_result(value)}"
            for key, value in groups.items()
        )
        summary_layout.addWidget(QLabel(group_line))
        warnings = payload.get("warnings", [])
        if warnings:
            summary_layout.addWidget(QLabel("Предупреждения:\n" + "\n".join(warnings)))
        tabs.addTab(summary, "Таблица и радар")

        compare_page = QWidget()
        compare_layout = QVBoxLayout(compare_page)
        compare_layout.setContentsMargins(0, 8, 0, 8)
        compare_card = QFrame()
        compare_card.setObjectName("contentCard")
        compare_card_layout = QVBoxLayout(compare_card)
        compare_card_layout.addWidget(QLabel("Сводные результаты", objectName="cardTitle"))
        compare_table = QTableWidget(1, 5)
        compare_table.setObjectName("comparisonSummary")
        compare_table.setHorizontalHeaderLabels(["Опыт", "Алгоритм", "Профиль", "Повтор", "I(A), баллы"])
        compare_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((190, 285, 220, 105)):
            compare_table.setColumnWidth(column, width)
        compare_table.verticalHeader().hide()
        compare_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        algorithm_name = (experiment.passport.get("algorithm", {}).get("name_ru") if experiment else None) or "—"
        repeat_id = (experiment.passport.get("repeat_id") if experiment else None) or "—"
        for column, value in enumerate((payload.get("experiment_id", "—"), algorithm_name,
                                        payload.get("profile_id", "—"), repeat_id, _display_result(integral))):
            compare_table.setItem(0, column, QTableWidgetItem(str(value)))
        compare_table.setMaximumHeight(105)
        compare_card_layout.addWidget(compare_table)
        compare_card_layout.addWidget(QLabel(
            "Показан текущий сохранённый опыт. Для двух опытов выберите серию; прямое сравнение "
            "разрешается только после проверки условий."
        ))
        compare_layout.addWidget(compare_card)
        compare_button = QPushButton("Выбрать серию для сравнения")
        compare_callback = getattr(parent, "manage_series", None)
        compare_button.setEnabled(callable(compare_callback))
        if callable(compare_callback):
            compare_button.clicked.connect(compare_callback)
        compare_card_layout.addWidget(compare_button)
        chart_card = QFrame()
        chart_card.setObjectName("contentCard")
        chart_card_layout = QVBoxLayout(chart_card)
        chart_card_layout.addWidget(QLabel("Горизонтальные полосы взвешенных вкладов", objectName="cardTitle"))
        chart_card_layout.addWidget(self.contribution_chart, 1)
        compare_layout.addWidget(chart_card, 1)
        contributions_export = QPushButton("Сохранить вклады PNG")
        contributions_export.clicked.connect(lambda: self.export_png(
            self.contribution_chart, "Сохранить вклады", "contributions.png"))
        compare_layout.addWidget(contributions_export)
        tabs.addTab(compare_page, "Сравнение серии")

        timeline_page = QWidget()
        timeline_layout = QVBoxLayout(timeline_page)
        timeline_legend = QLabel(
            "Состояния маршрута: зелёный — установлен и работоспособен; красный — установлен, "
            "но нарушен; серый — нет пути; фиолетовый — петля. Жёлтая штриховка — состояние "
            "неизвестно; бежевая штриховка — нет наблюдений. Для канала зелёный означает доступен, "
            "красный — недоступен. Пунктирная вертикаль — фактическое событие."
        )
        timeline_legend.setWordWrap(True)
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
        timeline_layout.addWidget(timeline_legend)
        tabs.addTab(timeline_page, "Диаграмма Ганта")

        explanation_page = QWidget()
        explanation_layout = QVBoxLayout(explanation_page)
        explanation_layout.setContentsMargins(0, 8, 0, 8)
        explanation_cards = QHBoxLayout()
        selected_card = QFrame()
        selected_card.setObjectName("contentCard")
        selected_layout = QVBoxLayout(selected_card)
        selected_layout.setContentsMargins(22, 18, 22, 18)
        selected_heading = QLabel("Выбранный показатель")
        selected_heading.setObjectName("cardTitle")
        selected_layout.addWidget(selected_heading)
        metric_picker = QComboBox()
        for key in metric_order:
            metric_picker.addItem(f"{METRIC_NAMES.get(key, key)} · {metrics[key].get('symbol', key)}", key)
        selected_layout.addWidget(metric_picker)
        metric_explanation = QLabel()
        metric_explanation.setWordWrap(True)
        metric_explanation.setAlignment(Qt.AlignmentFlag.AlignTop)
        selected_layout.addWidget(metric_explanation, 1)
        explanation_cards.addWidget(selected_card, 1)
        context_card = QFrame()
        context_card.setObjectName("contentCard")
        context_layout = QVBoxLayout(context_card)
        context_layout.setContentsMargins(22, 18, 22, 18)
        context_heading = QLabel("Контекст и ограничения")
        context_heading.setObjectName("cardTitle")
        context_layout.addWidget(context_heading)
        context_detail = QLabel(
            f"Источник: {(experiment.passport.get('data_origin') if experiment else payload.get('data_origin')) or '—'}\n"
            f"Профиль Ω: {payload.get('profile_id', '—')}\n"
            f"Вход v{payload.get('input_revision', '—')} · модель {payload.get('model_version', '—')}\n\n"
            "Оценка относится к указанному варианту и условиям опыта. Балл не является вероятностью "
            "успеха и не подтверждает выполнение всех технических требований.\n\n"
            + ("Предупреждения:\n• " + "\n• ".join(warnings) if warnings else "Предупреждений нет.")
        )
        context_detail.setWordWrap(True)
        context_detail.setAlignment(Qt.AlignmentFlag.AlignTop)
        context_layout.addWidget(context_detail, 1)
        explanation_cards.addWidget(context_card, 1)
        explanation_layout.addLayout(explanation_cards, 1)
        def show_metric_explanation() -> None:
            key = metric_picker.currentData()
            if key is None:
                metric_explanation.setText("Показатель не выбран.")
                return
            item = metrics[key]
            raw = item.get("raw", {})
            trace = item.get("trace") or {}
            lines = [
                f"Исходное значение: {_display_result(raw)} {raw.get('unit', '')}",
                f"Статус: {RESULT_STATUS_LABELS.get(raw.get('status'), raw.get('status', '—'))}",
                f"Частный балл Bᵢ(A): {_display_result(item.get('partial_score', {}))}",
                f"Вес wᵢ: {_display_number(item.get('weight'))}",
                f"Вклад Kᵢ(A): {_display_result(item.get('contribution', {}))}",
            ]
            if raw.get("reason"):
                lines.append(f"Причина: {raw['reason']}")
            if key == "P_ad" and trace.get("checks"):
                lines.append("Проверки перестроения:")
                for check in trace["checks"]:
                    lines.append(
                        f"• {check.get('check_id', '—')}: перестроение {_display_number(check.get('time_s'))} с; "
                        f"подтверждение {_display_number(check.get('confirmation_window_s'))} с отдельно"
                    )
                lines.append("Формулы (3.27)–(3.30). Подтверждение не прибавляется к перестроению.")
            else:
                formula = {"P_zad": "(3.14)–(3.15)", "P_pr": "(3.16)",
                           "P_pot": "(3.17)–(3.18)", "P_bal": "(3.19)–(3.23)",
                           "P_ust": "(3.24)–(3.26)", "P_res": "(3.31)"}.get(key)
                if formula:
                    lines.append(f"Формула: {formula}")
                lines.append("Исходные выборки и счётчики доступны в технической трассе.")
            metric_explanation.setText("\n\n".join(lines))
        metric_picker.currentIndexChanged.connect(show_metric_explanation)
        if metric_picker.findData("P_ad") >= 0:
            metric_picker.setCurrentIndex(metric_picker.findData("P_ad"))
        show_metric_explanation()
        trace_toggle = QPushButton("Показать техническую трассу")
        explanation_layout.addWidget(trace_toggle)
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
        explanation.setVisible(False)
        def toggle_trace() -> None:
            visible = not explanation.isVisible()
            explanation.setVisible(visible)
            trace_toggle.setText("Скрыть техническую трассу" if visible else "Показать техническую трассу")
        trace_toggle.clicked.connect(toggle_trace)
        explanation_layout.addWidget(explanation)
        tabs.addTab(explanation_page, "Пояснения")
        context_short = f"{payload.get('experiment_id', '—')} · Ω {payload.get('profile_id', '—')}"
        if experiment is not None:
            context_short += (f" · сеть {experiment.passport.get('network', {}).get('network_id', '—')}"
                              f" · сценарий {experiment.passport.get('scenario_id', '—')}")
        def update_result_heading(index: int) -> None:
            reasons = " ".join(str(item.get("raw", {}).get("reason") or "").lower()
                               for item in payload.get("metrics", {}).values())
            missing_observations = any(term in reasons for term in
                                       ("непол", "не зарегистр", "нет измерения", "пробел", "журнал"))
            titles = (
                "Полная оценка не определяется" if integral.get("status") == "not_applicable" else
                ("Результат при неполноте наблюдений" if missing_observations else
                 "Результат с неопределённостью") if integral.get("status") in ("interval", "unknown") else
                "Результаты текущего эксперимента",
                "Сравнение экспериментов серии",
                "Диаграмма Ганта: маршруты и каналы во времени",
                "Пояснения к результату",
            )
            descriptions = (
                context_short,
                "Сопоставимость зависит от сети, нагрузки, событий, окна наблюдения и профиля Ω.",
                f"{payload.get('experiment_id', '—')} · фактически наблюдавшиеся состояния · модельное время опыта",
                "Исходные наблюдения → показатели → частные баллы → интегральная оценка",
            )
            heading.setText(titles[index])
            subtitle.setText(descriptions[index])
            page_code = ("П06.6" if integral.get("status") == "not_applicable" else
                         "П06.5" if integral.get("status") in ("interval", "unknown") else
                         "П06.1") if index == 0 else f"П06.{index + 1}"
            code.setText(page_code)
        tabs.currentChanged.connect(update_result_heading)
        update_result_heading(0)
        footer = QFrame()
        footer.setObjectName("footerBar")
        exports = QHBoxLayout(footer)
        exports.setContentsMargins(28, 8, 28, 8)
        close = QPushButton("Назад")
        close_callback = getattr(parent, "close_assessment", None)
        close.clicked.connect(close_callback if callable(close_callback) else self.close)
        exports.addWidget(close)
        export_menu = QMenu(self)
        export_menu.addAction("Результат JSON", self.export_json)
        export_menu.addAction("Показатели CSV", self.export_csv)
        export_button = QPushButton("Экспорт результата")
        export_button.setMenu(export_menu)
        exports.addWidget(export_button)
        exports.addStretch()
        if saved and callable(getattr(parent, "assign_assessment_to_series", None)):
            series_button = QPushButton("Добавить в серию")
            series_button.clicked.connect(lambda: parent.assign_assessment_to_series(
                self.payload.get("assessment_id")))
            exports.addWidget(series_button)
        if callable(getattr(parent, "next_experiment", None)):
            next_button = QPushButton("Добавить следующий эксперимент")
            next_button.setProperty("variant", "primary")
            next_button.setEnabled(saved and getattr(parent, "active_series_id", None) is not None)
            next_button.clicked.connect(parent.next_experiment)
            exports.addWidget(next_button)
        if callable(getattr(parent, "manage_series", None)):
            series_management = QPushButton("Завершить серию…")
            series_management.setEnabled(saved and getattr(parent, "active_series_id", None) is not None)
            series_management.clicked.connect(parent.manage_series)
            exports.addWidget(series_management)
        layout.addWidget(footer)

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
        self.contribution_chart = ComparisonContributionChart([first, second], self)
        layout.addWidget(self.contribution_chart)
        layout.addWidget(QLabel("Цвета соответствуют показателям: " + "; ".join(
            f"{METRIC_LABELS[key]} ({key})" for key in METRIC_LABELS
        )))
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
        layout.addWidget(QLabel(
            "Серия объединяет сохранённые оценки; копия входного набора не является независимым "
            "статистическим повтором. Закрытие серии не удаляет записи и не пересчитывает их."
        ))
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
        closed = next((item for item in self.store.list_series() if item[0] == series_id), None)
        if self.parent_window is not None and self.parent_window.active_series_id == series_id:
            self.parent_window.active_series_id = None
        self.refresh()
        if closed is not None and self.parent_window is not None:
            self.accept()
            self.parent_window.show_series_complete(closed[1], closed[3])

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


PASSPORT_LABELS = {
    "experiment_id": "Идентификатор опыта", "scenario_id": "Сценарий",
    "repeat_id": "Номер повтора", "description_ru": "Описание",
    "data_origin": "Источник данных", "algorithm_id": "Алгоритм",
    "name_ru": "Наименование реализации", "implementation_version": "Версия",
    "routing_mode": "Режим маршрутизации", "implementation_sha256": "SHA-256 реализации",
    "settings": "Настройки алгоритма", "settings_completeness": "Полнота настроек",
    "note_ru": "Примечание", "execution_source": "Источник исполнения",
    "pak_version": "Версия ПАК", "time_mode": "Режим времени",
    "hardware_description": "Оборудование", "queue_discipline": "Дисциплина очереди",
    "buffer_limit_bytes": "Буфер, байт", "link_errors": "Ошибки каналов",
    "randomness": "Случайность", "used": "Используется",
    "network_id": "Идентификатор сети", "directed": "Направленные каналы",
    "orbital_description": "Описание орбитальной конфигурации",
    "node_id": "Узел", "node_type": "Тип узла", "plane_id": "Плоскость",
    "initial_up": "Начальная доступность", "link_id": "Канал",
    "node_from": "От узла", "node_to": "К узлу",
    "interface_from": "Исходящий интерфейс", "interface_to": "Входящий интерфейс",
    "link_type": "Тип соединения", "initial_capacity_bps": "Скорость, бит/с",
    "initial_propagation_delay_s": "Задержка, с", "initial_routing_cost": "Стоимость",
    "resource_allocation": "Выделение ресурса", "flow_id": "Поток",
    "source_node": "Узел отправителя", "destination_node": "Узел получателя",
    "transport": "Транспорт", "packetization": "Пакетизация",
    "generation": "Генерация потока", "payload_bytes": "Полезная нагрузка, байт",
    "network_header_bytes": "Заголовок, байт", "fragmentation": "Фрагментация",
    "transport_retransmission": "Повторы транспорта", "duplication": "Дублирование",
    "start_s": "Начало, с", "interval_s": "Интервал, с",
    "packet_count": "Число пакетов", "stop_s": "Конец, с",
    "nominal_payload_rate_bps": "Скорость полезной нагрузки, бит/с",
    "event_id": "Событие", "event_type": "Тип события",
    "object_type": "Тип объекта", "object_ids": "Объекты события",
    "scheduled_time_s": "Запланировано, с", "actual_time_s": "Фактически, с",
    "result": "Результат", "changes": "Изменения",
    "capture_start_s": "Начало регистрации, с", "capture_end_s": "Конец регистрации, с",
    "evaluation_start_s": "Начало оценки, с", "evaluation_end_s": "Конец оценки, с",
    "packet_cohort": "Совокупность пакетов", "delay_definition": "Определение задержки",
    "loss_timeout_s": "Порог потери, с", "goodput_definition": "Полезная скорость",
    "time_base": "Шкала времени", "time_origin": "Начало отсчёта",
    "timestamp_resolution_s": "Разрешение меток, с", "clock_error_bound_s": "Погрешность часов, с",
    "route_observation_mode": "Наблюдение маршрутов",
    "route_time_error_bound_s": "Погрешность маршрутов, с",
    "channel_nominal_interval_s": "Интервал каналов, с",
    "channel_counter_mode": "Режим счётчиков",
    "channel_categories_disjoint": "Категории не пересекаются",
    "channel_max_atomic_packet_bytes": "Максимальный пакет, байт",
    "balance": "Балансировка", "stability": "Устойчивость",
    "adaptivity": "Адаптивность", "checks": "Проверки",
    "selected_link_ids": "Выбранные каналы", "time_start_s": "Начало проверки, с",
    "horizon_s": "Горизонт, с", "confirmation_window_s": "Подтверждение, с",
    "format_name": "Формат", "schema_version": "Версия схемы",
    "file_contract": "Контракт файлов", "export_status": "Полнота выгрузки",
    "complete": "Комплект завершён", "data_logging_complete": "Регистрация полная",
    "lost_log_records": "Потерянные записи", "logging_gaps": "Пробелы регистрации",
    "extensions": "Дополнительные поля",
}

PASSPORT_ARRAY_COLUMNS = {
    "network.nodes": ("node_id", "node_type", "plane_id", "initial_up"),
    "network.links": ("link_id", "node_from", "node_to", "initial_capacity_bps"),
    "traffic.flows": ("flow_id", "source_node", "destination_node", "transport"),
    "scenario.events": ("event_id", "event_type", "actual_time_s", "result"),
    "measurement.stability.checks": ("check_id", "flow_id", "time_start_s", "horizon_s"),
    "measurement.adaptivity.checks": ("check_id", "flow_id", "event_id"),
}


class PassportEditor(QWidget):
    """Nine passport forms backed by one lossless JSON value."""

    changed = Signal()
    SECTION_TABS = (
        ("Общие сведения", "Паспорт: общие сведения и алгоритм"),
        ("Среда", "Паспорт: источник и среда выполнения"),
        ("Узлы", "Паспорт: узлы сети"),
        ("Соединения", "Паспорт: направленные соединения"),
        ("Потоки", "Паспорт: потоки и нагрузка"),
        ("События", "Паспорт: события сценария"),
        ("Наблюдение", "Паспорт: условия наблюдения"),
        ("Проверки", "Паспорт: устойчивость и адаптивность"),
        ("Полнота", "Паспорт: полнота регистрации и формат"),
    )

    def __init__(self, value: dict[str, Any]):
        super().__init__()
        self._data: dict[str, Any] = {}
        self._invalid_fields: set[tuple[str | int, ...]] = set()
        self._array_tables: dict[tuple[str, ...], QTableWidget] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.tabs = FullWidthTabs()
        self.tabs.tabBar().setExpanding(True)
        layout.addWidget(self.tabs)
        self.set_value(value)

    def _get(self, path: tuple[str | int, ...]) -> Any:
        node: Any = self._data
        for key in path:
            if isinstance(node, dict):
                node = node.get(key)
            elif isinstance(node, list) and isinstance(key, int) and key < len(node):
                node = node[key]
            else:
                return None
        return node

    def _assign(self, path: tuple[str | int, ...], value: Any) -> None:
        node: Any = self._data
        for key in path[:-1]:
            if isinstance(node, dict):
                node = node.setdefault(key, {})
            else:
                node = node[key]
        previous = node.get(path[-1]) if isinstance(node, dict) else node[path[-1]]
        if previous != value:
            node[path[-1]] = value
            for array_path, table in self._array_tables.items():
                if path[:len(array_path)] == array_path and len(path) > len(array_path):
                    row = path[len(array_path)]
                    if isinstance(row, int) and 0 <= row < table.rowCount():
                        columns = PASSPORT_ARRAY_COLUMNS[".".join(array_path)]
                        if path[-1] in columns:
                            column = columns.index(path[-1])
                            table.setItem(row, column, QTableWidgetItem("—" if value is None else str(value)))
            self.changed.emit()

    @staticmethod
    def _label(key: str) -> str:
        return PASSPORT_LABELS.get(key, key.replace("_", " ").capitalize())

    @staticmethod
    def _kind(key: str, value: Any) -> str:
        if isinstance(value, bool) or key in {"directed", "initial_up", "used", "complete",
                                                   "data_logging_complete", "channel_categories_disjoint"}:
            return "boolean"
        if isinstance(value, int) or key.endswith(("_count", "_index")) or key in {"repeat_id", "plane_id"}:
            return "integer"
        if isinstance(value, float) or key.endswith(("_s", "_bps", "_bytes")):
            return "number"
        if isinstance(value, list):
            return "array"
        return "string"

    def _field(self, key: str, value: Any, path: tuple[str | int, ...]) -> QWidget:
        cell = QWidget()
        cell.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        cell_layout = QVBoxLayout(cell)
        cell_layout.setContentsMargins(0, 0, 0, 0)
        cell_layout.setSpacing(3)
        caption = QLabel(self._label(key))
        caption.setObjectName("fieldLabel")
        cell_layout.addWidget(caption)
        kind = self._kind(key, value)
        if kind == "boolean":
            control = QComboBox()
            control.addItems(["Не задано", "Да", "Нет"])
            control.setCurrentIndex(0 if value is None else 1 if value else 2)
            control.currentIndexChanged.connect(
                lambda index, p=path: self._assign(p, None if index == 0 else index == 1))
        elif kind == "string" and (key.endswith("_ru") or len(str(value or "")) > 110):
            control = QPlainTextEdit(str(value or ""))
            control.setFixedHeight(72)
            control.textChanged.connect(lambda w=control, p=path: self._assign(p, w.toPlainText()))
        else:
            control = QLineEdit()
            control.setText(json.dumps(value, ensure_ascii=False) if kind == "array" else
                            "" if value is None else str(value))
            control.setPlaceholderText("Не задано" if value is None else "")
            def update(text: str, p=path, k=kind, w=control) -> None:
                try:
                    if k == "integer":
                        parsed = int(text) if text.strip() else None
                    elif k == "number":
                        parsed = float(text.replace(",", ".")) if text.strip() else None
                        if parsed is not None and not math.isfinite(parsed):
                            raise ValueError("Число должно быть конечным")
                    elif k == "array":
                        parsed = json.loads(text) if text.strip() else []
                        if not isinstance(parsed, list):
                            raise ValueError("Ожидается JSON-массив")
                    else:
                        parsed = text if text or self._get(p) is not None else None
                except (ValueError, TypeError):
                    self._invalid_fields.add(p)
                    w.setStyleSheet("border: 1px solid #c94343;")
                    w.setToolTip("Проверьте тип и значение поля")
                    return
                self._invalid_fields.discard(p)
                w.setStyleSheet("")
                w.setToolTip("")
                self._assign(p, parsed)
            control.textChanged.connect(update)
        control.setObjectName("passport." + ".".join(map(str, path)))
        cell_layout.addWidget(control)
        technical = QLabel(".".join(map(str, path)))
        technical.setObjectName("fieldKey")
        cell_layout.addWidget(technical)
        return cell

    def _fields(self, layout: QVBoxLayout, node: dict[str, Any], path: tuple[str | int, ...],
                keys: tuple[str, ...] | None = None, wide_keys: frozenset[str] = frozenset()) -> None:
        selected = [(key, node[key]) for key in (keys or tuple(node)) if key in node]
        scalars = [(key, value) for key, value in selected if not isinstance(value, dict)]
        if scalars:
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(12)
            row, column = 0, 0
            for key, value in scalars:
                field = self._field(key, value, path + (key,))
                if key in wide_keys:
                    if column:
                        row, column = row + 1, 0
                    grid.addWidget(field, row, 0, 1, 2)
                    row += 1
                else:
                    grid.addWidget(field, row, column)
                    column += 1
                    if column == 2:
                        row, column = row + 1, 0
            layout.addLayout(grid)
        for key, value in selected:
            if isinstance(value, dict):
                heading = QLabel(self._label(key))
                heading.setObjectName("cardTitle")
                layout.addWidget(heading)
                self._fields(layout, value, path + (key,))

    def _card(self, parent: QVBoxLayout, title: str, node: dict[str, Any] | None,
              path: tuple[str | int, ...], keys: tuple[str, ...] | None = None,
              wide_keys: frozenset[str] = frozenset()) -> None:
        card = QFrame()
        card.setObjectName("contentCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(22, 18, 22, 18)
        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        content.addWidget(heading)
        if isinstance(node, dict) and node:
            self._fields(content, node, path, keys, wide_keys)
        else:
            content.addWidget(QLabel("Поля пока не заданы. Их можно добавить в расширенном вводе."))
        content.addStretch()
        parent.addWidget(card)

    def _array_card(self, parent: QVBoxLayout, title: str, path: tuple[str, ...]) -> None:
        array = self._get(path)
        if not isinstance(array, list):
            array = []
        path_name = ".".join(path)
        columns = PASSPORT_ARRAY_COLUMNS[path_name]
        card = QFrame()
        card.setObjectName("contentCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 18, 22, 18)
        heading = QLabel()
        heading.setObjectName("cardTitle")
        card_layout.addWidget(heading)
        row = QHBoxLayout()
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([self._label(key) for key in columns])
        table.horizontalHeader().setStretchLastSection(True)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setMinimumHeight(260)
        self._array_tables[path] = table
        row.addWidget(table, 3)
        detail = QFrame()
        detail.setObjectName("contentCard")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 12, 16, 12)
        row.addWidget(detail, 2)
        card_layout.addLayout(row)

        def show_detail() -> None:
            while detail_layout.count():
                child = detail_layout.takeAt(0)
                if child.widget() is not None:
                    child.widget().deleteLater()
                elif child.layout() is not None:
                    child.layout().deleteLater()
            selected = table.currentRow()
            if 0 <= selected < len(array) and isinstance(array[selected], dict):
                label = QLabel(f"Запись {selected + 1}")
                label.setObjectName("cardTitle")
                detail_layout.addWidget(label)
                scroller = QScrollArea()
                scroller.setWidgetResizable(True)
                form = QWidget()
                form_layout = QVBoxLayout(form)
                self._fields(form_layout, array[selected], path + (selected,))
                form_layout.addStretch()
                scroller.setWidget(form)
                detail_layout.addWidget(scroller)
            else:
                detail_layout.addWidget(QLabel("Выберите запись или добавьте новую."))

        def refresh(selected: int = 0) -> None:
            heading.setText(f"{title} — записей: {len(array)}")
            table.setRowCount(len(array))
            for index, item in enumerate(array):
                for column, key in enumerate(columns):
                    value = item.get(key) if isinstance(item, dict) else None
                    table.setItem(index, column, QTableWidgetItem("—" if value is None else str(value)))
            if array:
                table.selectRow(min(selected, len(array) - 1))
            show_detail()

        table.itemSelectionChanged.connect(show_detail)
        actions = QHBoxLayout()
        add = QPushButton("Добавить запись")
        duplicate = QPushButton("Копировать запись")
        delete = QPushButton("Удалить запись")
        def add_item() -> None:
            template = ARRAY_TEMPLATES.get(path_name, {})
            if self._get(path) is None:
                self._assign(path, array)
            array.append(deepcopy(template))
            refresh(len(array) - 1)
            self.changed.emit()
        def duplicate_item() -> None:
            index = table.currentRow()
            if 0 <= index < len(array):
                array.insert(index + 1, deepcopy(array[index]))
                refresh(index + 1)
                self.changed.emit()
        def delete_item() -> None:
            index = table.currentRow()
            if 0 <= index < len(array):
                del array[index]
                refresh(min(index, len(array) - 1))
                self.changed.emit()
        add.clicked.connect(add_item)
        duplicate.clicked.connect(duplicate_item)
        delete.clicked.connect(delete_item)
        for button in (add, duplicate, delete):
            actions.addWidget(button)
        card_layout.addLayout(actions)
        parent.addWidget(card)
        refresh()

    def _section(self, index: int) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(12)
        if index == 0:
            cards = QHBoxLayout()
            self._card(cards, "Описание эксперимента", self._data, (),
                       ("experiment_id", "repeat_id", "scenario_id", "description_ru", "data_origin"),
                       frozenset({"scenario_id", "description_ru", "data_origin"}))
            self._card(cards, "Исследуемая реализация", self._get(("algorithm",)), ("algorithm",),
                       wide_keys=frozenset({"algorithm_id", "name_ru", "note_ru"}))
            layout.addLayout(cards)
        elif index == 1:
            self._card(layout, "Среда выполнения и источник", self._get(("environment",)), ("environment",))
        elif index == 2:
            self._card(layout, "Сеть", self._get(("network",)), ("network",),
                       ("network_id", "directed", "orbital_description"))
            self._array_card(layout, "Узлы сети", ("network", "nodes"))
        elif index == 3:
            self._array_card(layout, "Направленные соединения", ("network", "links"))
        elif index == 4:
            self._array_card(layout, "Потоки и пакетизация", ("traffic", "flows"))
        elif index == 5:
            self._array_card(layout, "События сценария", ("scenario", "events"))
        elif index == 6:
            measurement = self._get(("measurement",))
            keys = tuple(key for key in measurement if key not in {"balance", "stability", "adaptivity"}) if isinstance(measurement, dict) else ()
            self._card(layout, "Условия и полнота наблюдения", measurement, ("measurement",), keys)
        elif index == 7:
            for key in ("balance", "stability", "adaptivity"):
                self._card(layout, self._label(key), self._get(("measurement", key)), ("measurement", key),
                           tuple(k for k in (self._get(("measurement", key)) or {}) if k != "checks"))
            self._array_card(layout, "Проверки устойчивости", ("measurement", "stability", "checks"))
            self._array_card(layout, "Проверки адаптивности", ("measurement", "adaptivity", "checks"))
        else:
            self._card(layout, "Версия формата", self._data, (), ("format_name", "schema_version"))
            self._card(layout, "Полнота регистрации", self._get(("export_status",)), ("export_status",))
            self._card(layout, "Контракт четырёх файлов", self._get(("file_contract",)), ("file_contract",))
            self._card(layout, "Дополнительные поля паспорта", self._get(("extensions",)), ("extensions",))
        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def set_value(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise ValueError("Паспорт должен быть объектом")
        self._data = deepcopy(value)
        self._invalid_fields.clear()
        self._array_tables.clear()
        selected = max(self.tabs.currentIndex(), 0)
        self.tabs.blockSignals(True)
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.deleteLater()
        for index, (label, _) in enumerate(self.SECTION_TABS):
            self.tabs.addTab(self._section(index), label)
        self.tabs.setCurrentIndex(min(selected, self.tabs.count() - 1))
        self.tabs.blockSignals(False)

    def value(self) -> dict[str, Any]:
        if self._invalid_fields:
            path = ".".join(map(str, sorted(self._invalid_fields, key=str)[0]))
            raise ValueError(f"Исправьте значение поля паспорта: {path}")
        return deepcopy(self._data)

    def open_advanced_editor(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Расширенный ввод паспорта")
        dialog.resize(950, 680)
        layout = QVBoxLayout(dialog)
        editor = JsonTree(deepcopy(self._data), "Полный паспорт")
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)
        def accept() -> None:
            try:
                data = editor.value()
                if not isinstance(data, dict):
                    raise ValueError("Паспорт должен быть объектом")
            except (ValueError, TypeError) as exc:
                QMessageBox.warning(dialog, "Ошибка паспорта", str(exc))
                return
            self.set_value(data)
            self.changed.emit()
            dialog.accept()
        buttons.accepted.connect(accept)
        dialog.exec()


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
        if parent.isValid() or count <= 0 or row < 0 or row > self.rowCount():
            return False
        self.beginInsertRows(QModelIndex(), row, row + count - 1)
        for _ in range(count):
            self.rows.insert(row, {name: "" for name in self.header})
        self.endInsertRows()
        self.changed.emit()
        return True

    def removeRows(self, row: int, count: int, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid() or count <= 0 or row < 0 or row + count > self.rowCount():
            return False
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
        self.assessment_panel: AssessmentDialog | None = None
        self.assessment_return_page: QWidget | None = None
        self.pending_import: Experiment | None = None
        self.setWindowTitle("OPD — подготовка и оценка опыта")
        self.resize(1300, 850)
        self.setStyleSheet(UI_STYLESHEET)
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.pages = QStackedWidget()
        root_layout.addWidget(self.pages)
        self.start_page = self._navigation_page(
            "OPD", "Комплексная оценка алгоритмов маршрутизации", (("Начать", self.show_main_menu),),
            extra_widget=NetworkMark(),
        )
        self.main_menu_page = self._navigation_page(
            "Главное меню", "Выберите раздел. Все переходы расположены непосредственно на панели.", (
                ("Эксперименты", self.show_experiments),
                ("Справка", self.show_help), ("О программе", self.show_about), ("Выход", self.close),
            ),
        )
        self.experiments_page = self._navigation_page(
            "Эксперименты", "Один опыт — один комплект данных; серия объединяет несколько сохранённых опытов.", (
                ("Начать новый эксперимент", self.show_input_choices),
                ("Продолжить предыдущий", self.show_saved_results),
                ("Просмотреть результаты", self.show_saved_results),
                ("Назад в главное меню", self.show_main_menu),
            ),
        )
        self.input_page = self._navigation_page(
            "Новый опыт", "Новый набор сохраняется отдельной записью.", (
                ("Выбрать комплект", self.show_import_page),
                ("Открыть формы ввода", self.new),
                ("Назад", self.show_experiments),
            ),
        )
        import_content = QWidget()
        import_layout = QVBoxLayout(import_content)
        import_layout.setContentsMargins(0, 0, 0, 0)
        import_layout.addWidget(QLabel("Папка с исходным комплектом"))
        import_picker = QHBoxLayout()
        self.import_path = QLineEdit()
        self.import_path.setReadOnly(True)
        self.import_path.setPlaceholderText("Выберите папку с experiment.json и тремя CSV")
        import_picker.addWidget(self.import_path, 1)
        browse_import = QPushButton("Выбрать папку")
        browse_import.clicked.connect(self.import_folder)
        import_picker.addWidget(browse_import)
        import_layout.addLayout(import_picker)
        self.import_files_table = QTableWidget(4, 4)
        self.import_files_table.setHorizontalHeaderLabels(["Файл", "Содержание", "Наличие", "Число записей"])
        self.import_files_table.horizontalHeader().setStretchLastSection(True)
        self.import_files_table.verticalHeader().hide()
        self.import_files_table.setAlternatingRowColors(True)
        self.import_files_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.import_files_table.setMinimumHeight(270)
        self.import_files_table.setColumnWidth(0, 160)
        self.import_files_table.setColumnWidth(1, 480)
        self.import_files_table.setColumnWidth(2, 110)
        import_layout.addWidget(self.import_files_table)
        import_note = QLabel(
            "При выборе папки проверяются состав, формат и связи файлов. Готовность данных и профиля Ω "
            "к расчёту проверяется отдельно. Размер и SHA-256 принятого файла доступны в подсказке к строке."
        )
        import_note.setObjectName("infoNote")
        import_note.setWordWrap(True)
        import_layout.addWidget(import_note)
        self._fill_import_table(None)
        self.import_preview_page = self._navigation_page(
            "Состав импортированного комплекта",
            "Комплект одного эксперимента: один JSON и три CSV. Исходные файлы сохраняются без подмены.", (
                ("Открыть данные", self.accept_import_preview),
                ("Назад", self.show_input_choices),
            ), extra_widget=import_content,
        )
        self.import_open_button = next(button for button in self.import_preview_page.findChildren(QPushButton)
                                       if button.text() == "Открыть данные")
        self.import_open_button.setEnabled(False)
        self.saved_results = QComboBox()
        self.saved_results.setVisible(False)
        self.results_search = QLineEdit()
        self.results_search.setPlaceholderText("Поиск по опыту, алгоритму, сценарию или дате")
        self.results_model = QComboBox()
        self.results_model.addItem("Все модели", None)
        self.results_state = QComboBox()
        self.results_state.addItem("Все состояния", None)
        self._result_filters: dict[int, tuple[str, str]] = {}
        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels([
            "Опыт", "Алгоритм", "Сценарий", "Повтор", "Модель", "Состояние", "I(A)", "Сохранено",
        ])
        self.results_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((160, 180, 170, 70, 110, 110, 95)):
            self.results_table.setColumnWidth(column, width)
        self.results_table.verticalHeader().hide()
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setShowGrid(False)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.cellClicked.connect(
            lambda row, _column: self.saved_results.setCurrentIndex(row))
        self.results_table.itemSelectionChanged.connect(self.select_saved_result)
        self.results_search.textChanged.connect(self.filter_saved_results)
        self.results_model.currentIndexChanged.connect(lambda: self.filter_saved_results(self.results_search.text()))
        self.results_state.currentIndexChanged.connect(lambda: self.filter_saved_results(self.results_search.text()))
        results_content = QWidget()
        results_content_layout = QVBoxLayout(results_content)
        results_content_layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        filters.addWidget(self.results_search, 3)
        filters.addWidget(self.results_model, 1)
        filters.addWidget(self.results_state, 1)
        results_content_layout.addLayout(filters)
        results_content_layout.addWidget(self.results_table, 1)
        results_content_layout.addWidget(self.saved_results)
        self.results_page = self._navigation_page(
            "Результаты и черновики", "Оценки открываются без повторного расчёта; черновик можно продолжить", (
                ("Открыть выбранную оценку", self.open_selected_result),
                ("Продолжить черновик", self.open_draft),
                ("Серии", self.manage_series), ("Назад", self.show_experiments),
            ),
            extra_widget=results_content,
        )
        self.series_complete_summary = QLabel()
        self.series_complete_page = self._navigation_page(
            "Серия завершена", "", (
                ("Просмотреть результаты", self.show_saved_results),
                ("Вернуться к экспериментам", self.show_experiments),
            ), extra_widget=self.series_complete_summary,
        )
        help_text = QLabel(
            "1. Подготовьте один опыт: загрузите четыре файла или заполните те же сведения вручную.\n\n"
            "2. Выберите профиль оценивания Ω со шкалами и весами.\n\n"
            "3. Проверьте данные и запустите расчёт К0–К2.\n\n"
            "4. Просмотрите таблицу, радар, сравнение, диаграмму Ганта и пояснения.\n\n"
            "5. Сохраните результат; затем его можно открыть из истории без повторного расчёта."
        )
        help_text.setWordWrap(True)
        self.help_page = self._navigation_page(
            "Справка по работе с программой", "Один эксперимент — один набор наблюдений",
            (("Назад в главное меню", self.show_main_menu),), extra_widget=help_text,
        )
        about_text = QLabel(
            "Комплексная оценка эффективности алгоритмов маршрутизации в многоспутниковых системах связи.\n\n"
            "Основа расчёта — математическая модель главы 3 ТЗ-ОАМ-01.\n"
            "Программа оценивает готовые наблюдения; первичные данные создаются вне приложения."
        )
        about_text.setWordWrap(True)
        self.about_page = self._navigation_page(
            "О программе", "Сведения о назначении и математической основе",
            (("Назад в главное меню", self.show_main_menu),), extra_widget=about_text,
        )
        for page in (self.start_page, self.main_menu_page, self.experiments_page,
                     self.input_page, self.import_preview_page, self.results_page, self.series_complete_page,
                     self.help_page, self.about_page):
            self.pages.addWidget(page)
        self.editor_page = QWidget()
        self.editor_page.setObjectName("workspacePage")
        self.pages.addWidget(self.editor_page)
        layout = QVBoxLayout(self.editor_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        editor_masthead = QFrame()
        editor_masthead.setObjectName("masthead")
        editor_masthead.setFixedHeight(56)
        editor_masthead_layout = QHBoxLayout(editor_masthead)
        editor_masthead_layout.setContentsMargins(32, 0, 32, 0)
        editor_brand = QLabel("ОЦЕНКА МАРШРУТИЗАЦИИ")
        editor_brand.setObjectName("brand")
        editor_masthead_layout.addWidget(editor_brand)
        editor_masthead_layout.addStretch()
        self.retry_button = QPushButton("Повторить сохранение результата")
        self._set_retry_available(False)
        self.retry_button.clicked.connect(self.retry_assessment_save)
        editor_masthead_layout.addWidget(self.retry_button)
        utility_menu = QMenu(self)
        for label, callback in (
            ("Новый опыт", self.new), ("Импортировать четыре файла", self.import_folder),
            ("Открыть черновик", self.open_draft), ("Экспортировать четыре файла", self.export),
            ("История результатов", self.open_assessment),
            ("Сравнить результаты", self.compare_assessments), ("Управление сериями", self.manage_series),
            ("Расширенный ввод паспорта", lambda: self.passport.open_advanced_editor()),
            ("Создать резервную копию", self.backup_store), ("Восстановить копию", self.restore_store),
        ):
            utility_menu.addAction(label, callback)
        utility_button = QToolButton()
        utility_button.setObjectName("utilityMenu")
        utility_button.setText("Действия  ▾")
        utility_button.setMenu(utility_menu)
        utility_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        editor_masthead_layout.addWidget(utility_button)
        self.editor_code = QLabel("П05.1")
        self.editor_code.setObjectName("pageCode")
        editor_masthead_layout.addWidget(self.editor_code)
        layout.addWidget(editor_masthead)
        self.editor_heading = QLabel()
        self.editor_heading.setObjectName("pageTitle")
        self.editor_heading.setContentsMargins(30, 10, 30, 0)
        layout.addWidget(self.editor_heading)
        self.editor_subtitle = QLabel()
        self.editor_subtitle.setObjectName("pageSubtitle")
        self.editor_subtitle.setContentsMargins(30, 0, 30, 4)
        layout.addWidget(self.editor_subtitle)
        self.tabs = FullWidthTabs()
        self.tabs.tabBar().setExpanding(True)
        tabs_container = QWidget()
        tabs_container_layout = QVBoxLayout(tabs_container)
        tabs_container_layout.setContentsMargins(30, 0, 30, 0)
        tabs_container_layout.addWidget(self.tabs)
        layout.addWidget(tabs_container, 1)
        self.passport = PassportEditor(empty_passport())
        self.tabs.addTab(self.passport, "Паспорт и условия")
        self.tables = {}
        for name, title in (("packets.csv", "Пакеты"), ("routes.csv", "Маршруты"), ("channels.csv", "Каналы")):
            editor = TableEditor(name)
            self.tables[name] = editor
            self.tabs.addTab(editor, title)
        self.profile = JsonTree({"profile_id": "", "metrics": {}}, "Профиль оценивания Ω")
        load_profile = QPushButton("Загрузить Ω")
        load_profile.clicked.connect(self.import_profile)
        self.profile.layout().insertWidget(1, load_profile, alignment=Qt.AlignmentFlag.AlignRight)
        self.tabs.addTab(self.profile, "Профиль оценивания")
        self.tabs.currentChanged.connect(self._update_editor_heading)
        self.passport.tabs.currentChanged.connect(self._update_editor_heading)
        self._update_editor_heading()
        self.passport.changed.connect(self.mark_input_dirty)
        self.passport.changed.connect(self._update_editor_heading)
        self.profile.changed.connect(self.mark_dirty)
        for editor in self.tables.values():
            editor.changed.connect(self.mark_input_dirty)
        editor_actions = QHBoxLayout()
        editor_actions.setContentsMargins(30, 8, 30, 8)
        editor_actions.setSpacing(10)
        for label, callback in (("Назад", self.back_to_experiments),
                                ("Добавить следующий эксперимент", self.next_experiment),
                                ("Сохранить черновик", self.save_draft),
                                ("Проверить данные", self.check),
                                ("Запуск расчёта", self.run_assessment)):
            button = QPushButton(label)
            if label == "Запуск расчёта":
                button.setProperty("variant", "primary")
            button.clicked.connect(callback)
            if label == "Проверить данные":
                editor_actions.addStretch()
            editor_actions.addWidget(button)
        editor_footer = QFrame()
        editor_footer.setObjectName("footerBar")
        editor_footer.setLayout(editor_actions)
        layout.addWidget(editor_footer)
        self.statusBar().showMessage("Новый черновик. Пустые наблюдения не создаются автоматически.")
        self.pages.setCurrentWidget(self.start_page)

    def _update_editor_heading(self, _index: int = 0) -> None:
        section = self.passport.tabs.currentIndex()
        if self.tabs.currentWidget() is self.passport and 0 <= section < len(self.passport.SECTION_TABS):
            title = self.passport.SECTION_TABS[section][1]
            self.editor_code.setText(f"П05.{section + 1}")
        else:
            title = {
                1: "Пакеты: отправка и получение",
                2: "Маршруты: принятые решения",
                3: "Каналы: состояние и ресурсы",
                4: "Профиль оценивания: экспертные оценки и веса",
            }.get(self.tabs.currentIndex(), "Редактор исходных данных")
            self.editor_code.setText("П05")
        self.editor_heading.setText(title)
        experiment_id = self.passport._data.get("experiment_id", "")
        self.editor_subtitle.setText(
            f"{experiment_id or 'Новый опыт'} · единая форма для импорта и ручного ввода · данные текущего опыта"
        )

    def _set_retry_available(self, available: bool) -> None:
        self.retry_button.setEnabled(available)
        self.retry_button.setVisible(available)

    def _navigation_page(self, title: str, subtitle: str,
                         buttons: tuple[tuple[str, Any], ...],
                         extra_widget: QWidget | None = None) -> QWidget:
        page = QWidget()
        page.setObjectName("startPage" if title == "OPD" else "workspacePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        def action(label: str, callback: Any, primary: bool = False) -> QPushButton:
            button = QPushButton(label)
            button.setMinimumHeight(44)
            if primary:
                button.setProperty("variant", "primary")
            button.clicked.connect(callback)
            return button

        if title == "OPD":
            hero = QHBoxLayout()
            hero.setContentsMargins(64, 55, 64, 55)
            copy = QVBoxLayout()
            copy.addStretch()
            eyebrow = QLabel("НАСТОЛЬНАЯ ПРОГРАММА")
            eyebrow.setObjectName("heroEyebrow")
            copy.addWidget(eyebrow)
            heading = QLabel("ОЦЕНКА\nМАРШРУТИЗАЦИИ")
            heading.setObjectName("heroTitle")
            copy.addWidget(heading)
            description = QLabel("Комплексная оценка эффективности алгоритмов маршрутизации\n"
                                 "в многоспутниковых системах связи")
            description.setObjectName("heroSubtitle")
            description.setWordWrap(True)
            copy.addWidget(description)
            copy.addSpacing(35)
            start = action(*buttons[0], primary=True)
            start.setMinimumWidth(250)
            copy.addWidget(start, alignment=Qt.AlignmentFlag.AlignLeft)
            copy.addStretch()
            hero.addLayout(copy, 3)
            if extra_widget is not None:
                hero.addWidget(extra_widget, 2)
            layout.addLayout(hero, 1)
            return page

        codes = {"Главное меню": "П02", "Эксперименты": "П03", "Новый опыт": "П04",
                 "Состав импортированного комплекта": "П04а", "Результаты и черновики": "П07",
                 "Серия завершена": "Д05", "Справка по работе с программой": "С01",
                 "О программе": "С02"}
        masthead = QFrame()
        masthead.setObjectName("masthead")
        masthead.setFixedHeight(56)
        masthead_layout = QHBoxLayout(masthead)
        masthead_layout.setContentsMargins(32, 0, 32, 0)
        brand = QLabel("ОЦЕНКА МАРШРУТИЗАЦИИ")
        brand.setObjectName("brand")
        masthead_layout.addWidget(brand)
        masthead_layout.addStretch()
        code = QLabel(codes.get(title, ""))
        code.setObjectName("pageCode")
        masthead_layout.addWidget(code)
        layout.addWidget(masthead)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(42, 25, 42, 24)
        body_layout.setSpacing(12)
        display_title = {
            "Новый опыт": "Подготовка нового эксперимента",
            "Состав импортированного комплекта": "Загрузка данных ПАК",
            "Результаты и черновики": "Сохранённые эксперименты и результаты",
        }.get(title, title)
        heading = QLabel(display_title)
        heading.setObjectName("pageTitle")
        body_layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName("pageSubtitle")
        body_layout.addWidget(description)
        body_layout.addSpacing(14)

        footer_actions = [(label, callback) for label, callback in buttons
                          if label.startswith("Назад")]
        main_actions = [(label, callback) for label, callback in buttons
                        if not label.startswith("Назад")]
        if title in ("Эксперименты", "Новый опыт"):
            details = {
                "Начать новый эксперимент": ("Новый эксперимент", "Подготовить новый набор наблюдений: из четырёх файлов ПАК или через ручные формы."),
                "Продолжить предыдущий": ("Продолжить работу", "Открыть сохранённый черновик или продолжить ранее начатую серию."),
                "Просмотреть результаты": ("Посмотреть результаты", "Открыть рассчитанный опыт или серию. Повторный расчёт для просмотра не нужен."),
                "Выбрать комплект": ("Загрузить четыре файла", "experiment.json\npackets.csv\nroutes.csv\nchannels.csv"),
                "Открыть формы ввода": ("Ввести данные вручную", "Те же сведения и параметры.\nПолный паспорт, журнал пакетов, история маршрутов и каналов.\nНе форма из семи готовых показателей."),
            }
            grid = QGridLayout()
            grid.setSpacing(18)
            for index, (label, callback) in enumerate(main_actions):
                card = QFrame()
                card.setObjectName("contentCard")
                card.setMinimumHeight(320 if title == "Эксперименты" else 310)
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(24, 24, 24, 24)
                card_layout.setSpacing(14)
                card_title, card_text = details[label]
                card_heading = QLabel(card_title)
                card_heading.setObjectName("cardTitle")
                card_layout.addWidget(card_heading)
                card_description = QLabel(card_text)
                card_description.setObjectName("cardDescription")
                card_description.setWordWrap(True)
                card_layout.addWidget(card_description)
                card_layout.addStretch()
                card_layout.addWidget(action(label, callback, primary=index == 0))
                grid.addWidget(card, 0, index)
            body_layout.addLayout(grid)
            note = QLabel(
                "Новые эксперименты добавляются отдельно. Предыдущие данные, параметры оценивания и результаты сохраняются."
                if title == "Эксперименты" else
                "Оба способа открывают единый редактор. Количество записей определяется экспериментом, а не размером эталонного примера."
            )
            note.setObjectName("infoNote")
            note.setWordWrap(True)
            body_layout.addWidget(note)
            body_layout.addStretch()
        else:
            card = QFrame()
            card.setObjectName("contentCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(30, 28, 30, 28)
            card_layout.setSpacing(16)
            if title != "Результаты и черновики":
                card_layout.addStretch()
            if extra_widget is not None:
                card_layout.addWidget(extra_widget, 1 if title == "Результаты и черновики" else 0)
            if title == "Главное меню":
                card.setMinimumSize(700, 420)
                card.setMaximumWidth(760)
                card.setMaximumHeight(500)
                menu_title = QLabel("Комплексная оценка алгоритмов")
                menu_title.setObjectName("cardTitle")
                menu_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(menu_title)
                menu_description = QLabel("Исходные данные → расчёт → результаты")
                menu_description.setObjectName("cardDescription")
                menu_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(menu_description)
            if title == "Главное меню":
                menu_actions = dict(main_actions)
                primary = action("Эксперименты", menu_actions["Эксперименты"], primary=True)
                primary.setMinimumWidth(450)
                primary.setMinimumHeight(56)
                card_layout.addWidget(primary, alignment=Qt.AlignmentFlag.AlignHCenter)
                secondary = QHBoxLayout()
                for label in ("Справка", "О программе"):
                    secondary_button = action(label, menu_actions[label])
                    secondary_button.setMinimumHeight(52)
                    secondary.addWidget(secondary_button)
                card_layout.addLayout(secondary)
                exit_button = action("Выход", menu_actions["Выход"])
                exit_button.setMinimumHeight(52)
                card_layout.addWidget(exit_button)
                card_layout.addStretch()
                menu_note = QLabel("Одна рабочая область, без постоянного бокового меню")
                menu_note.setObjectName("cardDescription")
                menu_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card_layout.addWidget(menu_note)
            elif title == "Результаты и черновики":
                result_actions = QHBoxLayout()
                for index, (label, callback) in enumerate(main_actions):
                    result_actions.addWidget(action(label, callback, primary=index == 0))
                card_layout.addLayout(result_actions)
            elif title == "Состав импортированного комплекта":
                pass  # The open action belongs in the lower transition bar.
            else:
                for index, (label, callback) in enumerate(main_actions):
                    button = action(label, callback, primary=index == 0)
                    button.setMinimumWidth(240)
                    card_layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignHCenter)
            if title != "Результаты и черновики":
                card_layout.addStretch()
            if title == "Главное меню":
                body_layout.addWidget(card, 1, alignment=Qt.AlignmentFlag.AlignCenter)
            else:
                body_layout.addWidget(card, 1)
        layout.addWidget(body, 1)

        footer = QFrame()
        footer.setObjectName("footerBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(42, 9, 42, 9)
        if title == "Главное меню":
            footer_actions.append(("К титульной панели", lambda: self.pages.setCurrentWidget(self.start_page)))
        for label, callback in footer_actions:
            footer_layout.addWidget(action(label, callback))
        footer_layout.addStretch()
        if title == "Состав импортированного комплекта":
            for label, callback in main_actions:
                open_button = action(label, callback, primary=True)
                open_button.setMinimumWidth(220)
                footer_layout.addWidget(open_button)
        layout.addWidget(footer)
        return page

    def show_main_menu(self) -> None:
        self.pages.setCurrentWidget(self.main_menu_page)

    def show_experiments(self) -> None:
        self.pages.setCurrentWidget(self.experiments_page)

    def show_input_choices(self) -> None:
        self.pages.setCurrentWidget(self.input_page)

    def show_editor(self) -> None:
        self._update_editor_heading()
        self.pages.setCurrentWidget(self.editor_page)

    def show_help(self) -> None:
        self.pages.setCurrentWidget(self.help_page)

    def show_about(self) -> None:
        self.pages.setCurrentWidget(self.about_page)

    def back_to_experiments(self) -> None:
        if self._confirm_leave():
            self.show_experiments()

    def refresh_saved_results(self) -> None:
        self.saved_results.clear()
        self.results_table.setRowCount(0)
        self._result_filters.clear()
        self.results_model.blockSignals(True)
        self.results_state.blockSignals(True)
        self.results_model.clear()
        self.results_model.addItem("Все модели", None)
        self.results_state.clear()
        self.results_state.addItem("Все состояния", None)
        try:
            for assessment_id, draft_id, revision, model, stamp in self.store.list_assessments():
                payload, experiment, _ = self.store.load_assessment(assessment_id)
                passport = experiment.passport
                algorithm = passport.get("algorithm", {})
                integral = payload.get("integral", {})
                state = integral.get("status", "unknown")
                state_label = {"exact": "Точный", "interval": "Интервал",
                               "unknown": "Не определён", "not_applicable": "Неприменим"}.get(state, state)
                score = (_display_number(integral.get("value")) if integral.get("value") is not None
                         else f"{_display_number(integral.get('lower'))}–{_display_number(integral.get('upper'))}"
                         if state == "interval" else "—")
                self.saved_results.addItem(
                    f"{draft_id}, вход v{revision}, {model}, {stamp}", assessment_id,
                )
                row = self.results_table.rowCount()
                self.results_table.insertRow(row)
                display_stamp = stamp[:19].replace("T", " ") + (" UTC" if stamp.endswith("+00:00") else "")
                values = (
                    draft_id, algorithm.get("name_ru") or algorithm.get("algorithm_id") or "—",
                    passport.get("scenario_id") or "—", passport.get("repeat_id") or "—",
                    model, state_label, score, display_stamp,
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setToolTip(f"Вход v{revision} · Ω {payload.get('profile_id', '—')} · {stamp}")
                    self.results_table.setItem(row, column, item)
                self._result_filters[row] = (model, state)
                if self.results_model.findData(model) < 0:
                    self.results_model.addItem(model, model)
                if self.results_state.findData(state) < 0:
                    self.results_state.addItem(state_label, state)
                self.results_table.setRowHeight(row, 46)
            if self.results_table.rowCount():
                self.results_table.selectRow(0)
            self.filter_saved_results(self.results_search.text())
        except (sqlite3.Error, KeyError, ValueError, OSError) as exc:
            self._error(exc)
        finally:
            self.results_model.blockSignals(False)
            self.results_state.blockSignals(False)

    def filter_saved_results(self, query: str) -> None:
        needle = query.casefold().strip()
        selected_model = self.results_model.currentData()
        selected_state = self.results_state.currentData()
        for row in range(self.results_table.rowCount()):
            model, state = self._result_filters[row]
            visible = ((selected_model is None or model == selected_model)
                       and (selected_state is None or state == selected_state)
                       and any(needle in self.results_table.item(row, column).text().casefold()
                               for column in range(self.results_table.columnCount())))
            self.results_table.setRowHidden(row, not visible)
        selected = next((row for row in range(self.results_table.rowCount())
                         if not self.results_table.isRowHidden(row)), -1)
        if selected >= 0:
            self.results_table.selectRow(selected)
            self.saved_results.setCurrentIndex(selected)
        else:
            self.results_table.clearSelection()
            self.saved_results.setCurrentIndex(-1)

    def select_saved_result(self) -> None:
        row = self.results_table.currentRow()
        if row >= 0 and not self.results_table.isRowHidden(row):
            self.saved_results.setCurrentIndex(row)

    def show_saved_results(self) -> None:
        self.refresh_saved_results()
        self.pages.setCurrentWidget(self.results_page)

    def show_series_complete(self, title: str, count: int) -> None:
        self.series_complete_summary.setText(f"{title}: сохранено оценок — {count}")
        self.pages.setCurrentWidget(self.series_complete_page)

    def show_assessment(self, payload: dict[str, Any], saved: bool,
                        experiment: Experiment | None = None) -> None:
        current = self.pages.currentWidget()
        if current is not self.assessment_panel:
            self.assessment_return_page = current
        if self.assessment_panel is not None:
            self.pages.removeWidget(self.assessment_panel)
            self.assessment_panel.deleteLater()
        self.assessment_panel = AssessmentDialog(payload, saved, experiment, self)
        self.pages.addWidget(self.assessment_panel)
        self.pages.setCurrentWidget(self.assessment_panel)

    def close_assessment(self) -> None:
        if self.assessment_return_page is not None:
            self.pages.setCurrentWidget(self.assessment_return_page)
        else:
            self.show_experiments()

    def next_experiment(self) -> None:
        if self.assessment_worker is not None and self.assessment_worker.isRunning():
            QMessageBox.information(self, "Следующий эксперимент", "Дождитесь завершения текущего расчёта")
            return
        if self.active_series_id is None:
            QMessageBox.information(self, "Следующий эксперимент", "Сначала создайте или активируйте серию")
            return
        try:
            series = next((row for row in self.store.list_series() if row[0] == self.active_series_id), None)
        except sqlite3.Error as exc:
            self._error(exc)
            return
        if series is None or series[2] != "open":
            self.active_series_id = None
            QMessageBox.information(self, "Следующий эксперимент", "Активная серия закрыта или недоступна")
            return
        if self.dirty or self.draft_id is None:
            if not self.save_draft():
                return
        self.draft_id = None
        self.show_input_choices()

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
        self.show_assessment(payload, True, experiment)

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
        try:
            if self.draft_id is None:
                experiment = Experiment(empty_passport(), {name: [] for name in HEADERS})
                self._set_experiment(experiment)
                self.statusBar().showMessage("Несохранённые изменения отброшены")
            else:
                passport, tables, source, originals, profile, revision = self.store.load_raw(self.draft_id)
                experiment = Experiment(passport, tables, originals, source)
                self._set_experiment(experiment, profile)
                self.statusBar().showMessage(
                    f"Несохранённые изменения отброшены; восстановлена версия {revision} черновика {self.draft_id}"
                )
        except (KeyError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)
            return False
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
        self.pending_import = None
        self.import_path.setText(folder)
        self._fill_import_table(None, Path(folder))
        self.import_open_button.setEnabled(False)
        self.pages.setCurrentWidget(self.import_preview_page)
        try:
            experiment = read_folder(folder)
        except (DataError, OSError, ValueError) as exc:
            self._error(exc)
            return
        self.show_import_preview(experiment, folder)

    def show_import_page(self) -> None:
        self.pending_import = None
        self.import_path.clear()
        self._fill_import_table(None)
        self.import_open_button.setEnabled(False)
        self.pages.setCurrentWidget(self.import_preview_page)

    def _fill_import_table(self, experiment: Experiment | None, folder: Path | None = None) -> None:
        descriptions = {
            "experiment.json": "Паспорт, сеть, сценарий и правила наблюдения",
            "packets.csv": "Журнал отправок и получений",
            "routes.csv": "История установленных маршрутов",
            "channels.csv": "Интервальные состояния и объёмы",
        }
        for row, (name, description) in enumerate(descriptions.items()):
            content = experiment.original_files.get(name) if experiment else None
            found = content is not None or (folder is not None and (folder / name).is_file())
            count = len(experiment.tables[name]) if content is not None and name != "experiment.json" else None
            values = (name, description, "Найден" if found else "Отсутствует" if folder else "Не выбран",
                      str(count) if count is not None else "—")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if content is not None:
                    item.setToolTip(f"Размер: {len(content)} байт; SHA-256: {hashlib.sha256(content).hexdigest()}")
                self.import_files_table.setItem(row, column, item)

    def show_import_preview(self, experiment: Experiment, folder: str | Path | None = None) -> None:
        self.pending_import = experiment
        self.import_path.setText(str(folder) if folder is not None else "Комплект выбран")
        self._fill_import_table(experiment)
        self.import_open_button.setEnabled(True)
        self.pages.setCurrentWidget(self.import_preview_page)

    def accept_import_preview(self) -> None:
        experiment = self.pending_import
        if experiment is None:
            self.show_input_choices()
            return
        self.pending_import = None
        self.draft_id = None
        self._set_experiment(experiment)
        self.dirty = True  # The import is not yet persisted.
        self.setWindowModified(True)
        self.show_editor()
        self.statusBar().showMessage(
            f"Импортирован {experiment.experiment_id}; исходные файлы сохранятся с черновиком"
        )

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
            profile_errors = validate_profile(profile)
            if profile_errors:
                raise ValueError("Профиль Ω не проходит проверку:\n" +
                                 "\n".join(map(str, profile_errors[:10])))
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
            report = validate_profile(profile) + validate_experiment(experiment, complete=False)
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
            profile_errors = validate_profile(profile)
            if profile_errors:
                self.tabs.setCurrentWidget(self.profile)
                QMessageBox.warning(
                    self, "Нужен профиль Ω",
                    "Для расчёта загрузите профиль кнопкой «Загрузить Ω» или заполните вкладку "
                    "«Профиль оценивания». Пример для синтетических опытов находится в "
                    "configs/examples/profile_demo.json; его шкалы не утверждены для реальных данных.\n\n" +
                    "\n".join(map(str, profile_errors[:10])),
                )
                return
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
        self._set_retry_available(False)
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
        self.show_assessment(payload, True, experiment)

    def _assessment_failed(self, exc: object) -> None:
        if isinstance(exc, CalculationCancelled):
            self.statusBar().showMessage(str(exc) + "; результат не сохранён")
            return
        if isinstance(exc, ResultPersistenceError):
            self.unsaved_assessment = exc
            self._set_retry_available(True)
            self.statusBar().showMessage("Расчёт выполнен, результат не сохранён")
            answer = QMessageBox.critical(
                self, "Результат не сохранён",
                str(exc) + "\nРасчёт уже выполнен; можно повторить только сохранение.",
                QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Close,
            )
            if answer == QMessageBox.StandardButton.Retry:
                self.retry_assessment_save()
            elif self.running_experiment is not None:
                self.show_assessment(exc.payload, False, self.running_experiment)
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
        self._set_retry_available(False)
        self.statusBar().showMessage(f"Результат сохранён: {assessment_id}")
        _, experiment, _ = self.store.load_assessment(assessment_id)
        self.show_assessment(pending.payload, True, experiment)

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
        self.show_assessment(payload, True, experiment)

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
                self._set_retry_available(False)
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
                self._set_retry_available(False)
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
