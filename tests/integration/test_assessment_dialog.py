from __future__ import annotations

import os
import json
from pathlib import Path
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QTableWidget, QTabWidget

from scanner.core import calculate
from scanner.gui import (
    AssessmentDialog, MainWindow, NetworkMark, PassportEditor, SeriesManagerDialog, TableEditor,
)
from scanner.io.format import read_folder


ROOT = Path(__file__).resolve().parents[2]
CONTROL_ARCHIVE = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"

def test_result_table_preserves_unknown_and_open_interval_states() -> None:
    app = QApplication.instance() or QApplication([])
    payload = {
        "experiment_id": "EX1", "profile_id": "OMEGA", "model_version": "model-v1",
        "input_revision": 2, "profile_version": 1,
        "integral": {"status": "unknown", "value": None}, "groups": {}, "warnings": [],
        "metrics": {
            "P_zad": {
                "symbol": "P_zad", "direction": "min", "raw": {
                    "status": "unknown", "value": None, "lower": None, "upper": None,
                    "unit": "s", "reason": "нет измерения", "lower_open": False,
                    "upper_open": False,
                }, "normalized": {"status": "unknown", "value": None},
                "partial_score": {"status": "unknown", "value": None}, "weight": "0.2",
                "contribution": {"status": "unknown", "value": None},
            },
            "P_ad": {
                "symbol": "P_ad", "direction": "min", "raw": {
                    "status": "interval", "value": None, "lower": "1", "upper": None,
                    "unit": "s", "reason": "наблюдение прервано", "lower_open": True,
                    "upper_open": True,
                }, "normalized": {"status": "interval", "value": None},
                "partial_score": {"status": "exact", "value": "100"}, "weight": "0.2",
                "contribution": {"status": "exact", "value": "20"},
            },
        },
    }
    dialog = AssessmentDialog(payload, True)
    table = dialog.findChild(QTableWidget)
    assert table is not None
    assert table.rowCount() == 2
    assert table.item(0, 1).text() == "—"
    assert table.item(0, 4).text() == "unknown"
    assert table.item(1, 1).text() == "(1, ∞)"
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_result_views_render_with_a_saved_control_experiment(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        names = archive.namelist()
        passport_name = next(name for name in names if name.endswith("EXP01_REACTIVE_4/experiment.json"))
        prefix = passport_name.rsplit("/", 1)[0] + "/"
        folder = tmp_path / "control"
        folder.mkdir()
        for name in names:
            if name.startswith(prefix):
                (folder / name.removeprefix(prefix)).write_bytes(archive.read(name))
        profile_name = next(name for name in names if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    experiment = read_folder(folder)
    payload = calculate(experiment, profile).as_dict()
    payload.update({"input_revision": 1, "profile_version": 1, "assessment_id": "test"})
    payload["profile"] = profile
    dialog = AssessmentDialog(payload, True, experiment)
    dialog.show()
    app.processEvents()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None and tabs.count() == 4
    assert dialog.timeline_chart is not None
    assert dialog.radar_chart.grab().width() > 100
    tabs.setCurrentIndex(2)
    app.processEvents()
    assert dialog.timeline_chart.grab().width() > 100
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_main_window_navigation_keeps_all_stages_in_one_window(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "drafts.sqlite3")
    assert window.pages.currentWidget() is window.start_page
    mark = window.findChild(NetworkMark)
    assert mark is not None and mark.grab().width() >= 220
    assert window.findChildren(QPushButton)
    assert {button.text() for button in window.main_menu_page.findChildren(QPushButton)} == {
        "Эксперименты", "Справка", "О программе", "Выход",
    }
    assert {button.text() for button in window.editor_page.findChildren(QPushButton)}.issuperset({
        "Назад", "Сохранить черновик", "Проверить данные", "Запуск расчёта",
    })

    def click(page, label: str) -> None:
        button = next(button for button in page.findChildren(QPushButton) if button.text() == label)
        button.click()

    click(window.start_page, "Начать")
    assert window.pages.currentWidget() is window.main_menu_page
    click(window.main_menu_page, "Эксперименты")
    assert window.pages.currentWidget() is window.experiments_page
    click(window.experiments_page, "Начать новый эксперимент")
    assert window.pages.currentWidget() is window.input_page
    click(window.input_page, "Открыть пустые формы")
    assert window.pages.currentWidget() is window.editor_page
    series_id = window.store.create_series("Продолжение опыта")
    window.active_series_id = series_id
    window.draft_id = "before-next"
    window.dirty = True
    window.next_experiment()
    assert window.pages.currentWidget() is window.input_page
    assert any(row[0] == "before-next" for row in window.store.list_drafts())
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    manager = SeriesManagerDialog(window.store, window)
    manager.series_combo.setCurrentIndex(manager.series_combo.findData(series_id))
    manager.close_series()
    assert window.pages.currentWidget() is window.series_complete_page
    assert "сохранено оценок — 0" in window.series_complete_summary.text()
    window.show_saved_results()
    assert window.pages.currentWidget() is window.results_page
    window.show_assessment({"experiment_id": "display-only", "metrics": {}, "groups": {},
                            "integral": {"status": "unknown", "value": None}, "warnings": []},
                           True)
    assert window.pages.currentWidget() is window.assessment_panel
    window.close_assessment()
    assert window.pages.currentWidget() is window.results_page
    window.close()
    window.deleteLater()
    app.processEvents()


def test_passport_editor_uses_local_forms_and_round_trips_all_sections(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    original = read_folder(reference_folder).passport
    editor = PassportEditor(original)
    assert editor.tabs.count() == 7
    assert editor.value() == original
    editor.deleteLater()
    app.processEvents()


def test_table_editor_supports_add_copy_paste_and_delete() -> None:
    app = QApplication.instance() or QApplication([])
    editor = TableEditor("packets.csv")
    assert {button.text() for button in editor.findChildren(QPushButton)} == {
        "Добавить строку", "Удалить строки", "Копировать", "Вставить диапазон",
    }
    assert editor.model.insertRows(0, 2)
    editor.model.setData(editor.model.index(0, 1), "FLOW-1")
    editor.model.setData(editor.model.index(0, 2), "42")
    editor.view.selectionModel().select(
        editor.model.index(0, 1), QItemSelectionModel.SelectionFlag.Select,
    )
    editor.view.selectionModel().select(
        editor.model.index(0, 2), QItemSelectionModel.SelectionFlag.Select,
    )
    editor.copy()
    assert QApplication.clipboard().text() == "FLOW-1\t42"
    editor.view.setCurrentIndex(editor.model.index(1, 1))
    editor.paste()
    assert editor.model.rows[1]["flow_id"] == "FLOW-1"
    assert editor.model.rows[1]["packet_id"] == "42"
    editor.view.selectRow(1)
    editor.delete_rows()
    assert editor.model.rowCount() == 1
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_import_preview_shows_original_four_file_composition(
    reference_folder: Path, tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "import-preview.sqlite3")
    experiment = read_folder(reference_folder)
    window.show_import_preview(experiment)
    assert window.pages.currentWidget() is window.import_preview_page
    assert window.import_files_table.rowCount() == 4
    assert window.import_files_table.item(0, 2).text()
    window.accept_import_preview()
    assert window.pages.currentWidget() is window.editor_page
    assert window.dirty and len(window.original_files) == 4
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()
