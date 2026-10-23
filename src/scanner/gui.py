"""Stage-two Qt editor for the complete four-block input and profile Ω."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QMessageBox,
    QPushButton, QTableView, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget, QFileDialog,
)

from .io.format import DataError, export_four, from_manual, read_folder
from .io.model import Experiment, HEADERS, file_contract
from .io.profile import validate_profile
from .io.profile import DIRECTIONS, METRICS
from .io.storage import DraftStore
from .io.validation import validate_experiment


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
        self.setWindowTitle("OPD — редактор данных опыта")
        self.resize(1300, 850)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        toolbar = QHBoxLayout()
        for label, callback in (("Новый", self.new), ("Импорт 4 файлов", self.import_folder),
                                ("Загрузить Ω", self.import_profile),
                                ("Открыть черновик", self.open_draft), ("Сохранить черновик", self.save_draft),
                                ("Проверить данные", self.check), ("Экспорт 4 файлов", self.export)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        layout.addLayout(toolbar)
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

    def closeEvent(self, event) -> None:
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
