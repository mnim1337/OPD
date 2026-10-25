"""Qt application for editing inputs and running saved K0–K2 assessments."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QTableView, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QFileDialog, QTableWidget, QTableWidgetItem,
)

from .io.format import DataError, export_four, from_manual, read_folder
from .io.model import Experiment, HEADERS, file_contract
from .io.profile import validate_profile
from .io.profile import DIRECTIONS, METRICS
from .io.storage import DraftStore
from .io.validation import validate_experiment
from .core.dispatcher import DispatchError, ResultPersistenceError, calculate_and_save


class AssessmentDialog(QDialog):
    def __init__(self, payload: dict[str, Any], saved: bool, experiment: Experiment | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.payload = payload
        self.setWindowTitle("Результат оценки")
        self.resize(1050, 600)
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
            f"Итог: {total if total is not None else integral.get('status', 'неизвестно')} баллов из 100 · {state}"
        )
        layout.addWidget(QLabel("\n".join(context_lines)))
        table = QTableWidget(0, 10)
        table.setHorizontalHeaderLabels(["Показатель", "Исходное значение", "Единица", "Направление",
                                         "Статус", "Нормализация", "Bᵢ(A)", "wᵢ", "Kᵢ(A)", "Пояснение"])
        table.horizontalHeader().setStretchLastSection(True)
        for key, metric in payload.get("metrics", {}).items():
            row = table.rowCount()
            table.insertRow(row)
            raw = metric.get("raw", {})
            normalized = metric.get("normalized", {})
            if raw.get("value") is not None:
                raw_value = raw["value"]
            elif raw.get("status") == "interval":
                lower = str(raw.get("lower"))
                upper = str(raw.get("upper")) if raw.get("upper") is not None else "∞"
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
                normalized.get("value") if normalized.get("value") is not None else normalized.get("status", ""),
                metric.get("partial_score", {}).get("value") or metric.get("partial_score", {}).get("status", ""),
                metric.get("weight", ""),
                metric.get("contribution", {}).get("value") or metric.get("contribution", {}).get("status", ""),
                raw.get("reason") or normalized.get("reason") or "",
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(str(value)))
        layout.addWidget(table)
        groups = payload.get("groups", {})
        group_line = "Группы: " + "; ".join(
            f"{key} = {value.get('value') if value.get('value') is not None else value.get('status')}"
            for key, value in groups.items()
        )
        layout.addWidget(QLabel(group_line))
        warnings = payload.get("warnings", [])
        if warnings:
            layout.addWidget(QLabel("Предупреждения:\n" + "\n".join(warnings)))
        exports = QHBoxLayout()
        for label, callback in (("Сохранить JSON", self.export_json),
                                ("Сохранить CSV", self.export_csv)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            exports.addWidget(button)
        layout.addLayout(exports)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

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
        self.retry_button: QPushButton | None = None
        self.setWindowTitle("OPD — подготовка и оценка опыта")
        self.resize(1300, 850)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()
        for label, callback in (("Новый", self.new), ("Импорт 4 файлов", self.import_folder),
                                ("Загрузить Ω", self.import_profile),
                                ("Открыть черновик", self.open_draft), ("Сохранить черновик", self.save_draft),
                                ("Проверить данные", self.check), ("Запуск расчёта", self.run_assessment),
                                ("История результатов", self.open_assessment),
                                ("Резервная копия", self.backup_store),
                                ("Экспорт 4 файлов", self.export)):
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
        self.statusBar().showMessage("Новый черновик. Пустые наблюдения не создаются автоматически.")

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
        try:
            experiment, profile = self._experiment()
            draft_id = self.draft_id or str(experiment.passport.get("experiment_id") or "").strip()
            if not draft_id:
                draft_id, ok = QInputDialog.getText(self, "Идентификатор опыта", "Имя записи:")
                if not ok:
                    return
            if not draft_id:
                raise ValueError("Нужен непустой идентификатор записи")
            assessment_id, revision, payload = calculate_and_save(
                self.store, draft_id, experiment, profile,
            )
            self.draft_id = draft_id
            self.dirty = False
            self.setWindowModified(False)
            self.unsaved_assessment = None
            self.retry_button.setEnabled(False)
            self.statusBar().showMessage(
                f"Расчёт сохранён: {assessment_id}; вход {draft_id}, версия {revision}"
            )
            AssessmentDialog(payload, True, experiment, self).exec()
        except ResultPersistenceError as exc:
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
            else:
                AssessmentDialog(exc.payload, False, experiment, self).exec()
        except (DataError, DispatchError, OSError, ValueError, sqlite3.Error) as exc:
            self._error(exc)

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

    def closeEvent(self, event) -> None:
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
