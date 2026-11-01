from __future__ import annotations

import os
import json
import copy
import sqlite3
import time
import pytest
from pathlib import Path
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QMessageBox, QPushButton, QInputDialog, QLabel, QLineEdit, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QComboBox, QDialogButtonBox,
)

from scanner.core import calculate
from scanner.core.dispatcher import calculate_and_save
import scanner.gui as gui_module
from scanner.gui import (
    AssessmentDialog, AssessmentWorker, ComparisonDialog, MainWindow, NetworkMark, PassportEditor,
    SeriesManagerDialog, RepeatStatisticsDialog, TableEditor, TimelineChart, ProfileEditor,
)
from scanner.io.format import read_folder
from scanner.io.profile import validate_profile
from scanner.io.storage import DraftStore


ROOT = Path(__file__).resolve().parents[2]
CONTROL_ARCHIVE = ROOT / "requirements/эксперимент 5 штук/PAK_5_experiments_with_validation.zip"


def test_hidden_table_rows_remain_in_calculation_input(reference_folder: Path, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    imported = read_folder(reference_folder)
    window = MainWindow(tmp_path / "hidden-rows.sqlite3")
    window._set_experiment(imported)
    packets = window.tables["packets.csv"]
    expected_count = len(imported.tables["packets.csv"])
    packets.search.setText("no-such-packet")
    assert packets.model.rowCount() == expected_count
    assert all(packets.view.isRowHidden(index) for index in range(expected_count))
    reconstructed, _ = window._experiment()
    assert len(reconstructed.tables["packets.csv"]) == expected_count
    assert reconstructed.tables["packets.csv"][-1] == imported.tables["packets.csv"][-1]
    window.close()
    window.deleteLater()
    app.processEvents()

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
            "P_res": {
                "symbol": "P_res", "direction": "max", "raw": {
                    "status": "not_applicable", "value": None, "lower": None, "upper": None,
                    "unit": "%", "reason": "нет применимых событий", "lower_open": False,
                    "upper_open": False,
                }, "normalized": {"status": "not_applicable", "value": None},
                "partial_score": {"status": "not_applicable", "value": None}, "weight": "0.2",
                "contribution": {"status": "not_applicable", "value": None},
            },
        },
    }
    dialog = AssessmentDialog(payload, True)
    assert dialog.findChild(QLabel, "pageTitle").text() == "Результат при неполноте наблюдений"
    assert dialog.findChild(QLabel, "pageCode").text() == "П06.5"
    assert any("Неизвестные значения не заменяются нулём" in label.text()
               for label in dialog.findChildren(QLabel))
    table = dialog.findChild(QTableWidget, "resultTable")
    assert table is not None
    assert table.rowCount() == 3
    assert table.item(0, 1).text() == "не определено"
    assert table.item(0, 4).text() == "не определено"
    assert table.item(1, 1).text() == "(1, ∞)"
    assert table.item(2, 4).text() == "неприменимо"
    assert any(label.text() == "не определено" for label in dialog.findChildren(QLabel))
    dialog.close()
    dialog.deleteLater()
    payload["integral"] = {"status": "not_applicable", "value": None}
    not_applicable = AssessmentDialog(payload, True)
    assert not_applicable.findChild(QLabel, "pageTitle").text() == "Полная оценка не определяется"
    assert not_applicable.findChild(QLabel, "pageCode").text() == "П06.6"
    assert any("не исключается с автоматическим перераспределением веса" in label.text()
               for label in not_applicable.findChildren(QLabel))
    not_applicable.close()
    not_applicable.deleteLater()
    app.processEvents()


def test_radar_does_not_close_an_unknown_axis_through_zero() -> None:
    app = QApplication.instance() or QApplication([])
    exact_metrics = {
        key: {"partial_score": {"status": "exact", "value": "65"}}
        for key in gui_module.METRIC_LABELS
    }

    def tinted_polygon_pixels(metrics) -> int:
        chart = gui_module.RadarChart({"metrics": metrics})
        chart.resize(380, 360)
        chart.show()
        app.processEvents()
        image = chart.grab().toImage()
        count = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if (210 <= color.red() <= 222 and 224 <= color.green() <= 236
                        and 233 <= color.blue() <= 246):
                    count += 1
        chart.close()
        chart.deleteLater()
        app.processEvents()
        return count

    assert tinted_polygon_pixels(exact_metrics) > 500
    with_unknown = {key: dict(metric) for key, metric in exact_metrics.items()}
    with_unknown["P_zad"] = {"partial_score": {"status": "unknown", "value": None}}
    assert tinted_polygon_pixels(with_unknown) < 100


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
    metric_order = ["P_zad", "P_pr", "P_pot", "P_bal", "P_ust", "P_ad", "P_res"]
    assert list(payload["metrics"]) == metric_order
    payload["metrics"] = dict(reversed(list(payload["metrics"].items())))
    dialog = AssessmentDialog(payload, True, experiment)
    dialog.show()
    app.processEvents()
    tabs = dialog.findChild(QTabWidget)
    assert tabs is not None and [tabs.tabText(index) for index in range(tabs.count())] == [
        "Таблица и радар", "Сравнение серии", "Диаграмма Ганта", "Пояснения",
    ]
    assert dialog.timeline_chart is not None
    assert dialog.radar_chart.grab().width() > 100
    table = dialog.findChild(QTableWidget, "resultTable")
    assert table is not None and table.rowCount() == 7
    assert all(table.isColumnHidden(column) for column in (3, 4, 5, 9))
    detail_toggle = next(button for button in dialog.findChildren(QPushButton)
                         if button.text() == "Показать статус, нормализацию и пояснение")
    detail_toggle.click()
    assert all(not table.isColumnHidden(column) for column in (3, 4, 5, 9))
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        "Показатель", "Исходное значение", "Единица", "Направление", "Статус",
        "Нормализация", "Bᵢ(A)", "wᵢ", "Kᵢ(A)", "Пояснение",
    ]
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        f"{gui_module.METRIC_NAMES[key]} · {payload['metrics'][key].get('symbol', key)}"
        for key in metric_order
    ]
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    context = next(label for label in labels if label.startswith("Опыт:"))
    assert all(part in context for part in ("Алгоритм:", "сеть:", "сценарий:", "повтор:",
                                             "источник:", "Ω:", "вход v1", "баллов из 100"))
    assert experiment.experiment_id in context and payload["model_version"] in context
    assert str(experiment.passport["network"]["network_id"]) in context
    group_summary = next(label for label in labels if label.startswith("Группы:"))
    assert len(payload["groups"]) == 4 and group_summary.count(";") == 3
    tabs.setCurrentIndex(2)
    app.processEvents()
    assert dialog.findChild(QLabel, "pageTitle").text() == "Диаграмма Ганта: маршруты и каналы во времени"
    assert dialog.findChild(QLabel, "pageCode").text() == "П06.3"
    assert dialog.timeline_chart.grab().width() > 100
    if dialog.flow_filter.count() > 1:
        flow_id = dialog.flow_filter.itemData(1)
        dialog.flow_filter.setCurrentIndex(1)
        assert dialog.timeline_chart.flow_filter == flow_id
        assert all(kind != "route" or name == flow_id for kind, name, _ in dialog.timeline_chart._rows())
    if dialog.link_filter.count() > 1:
        link_id = dialog.link_filter.itemData(1)
        dialog.link_filter.setCurrentIndex(1)
        assert dialog.timeline_chart.link_filter == link_id
        assert all(kind != "channel" or name == link_id for kind, name, _ in dialog.timeline_chart._rows())
    if dialog.event_zoom.count() > 1:
        full_interval = dialog.timeline_chart.time_window
        dialog.event_zoom.setCurrentIndex(1)
        assert dialog.timeline_chart.time_window != full_interval
    tabs.setCurrentIndex(3)
    assert dialog.findChild(QLabel, "pageTitle").text() == "Пояснения к результату"
    assert any("подтверждение" in label.text() for label in dialog.findChildren(QLabel))
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_timeline_marks_unrecorded_model_time(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart(read_folder(reference_folder))
    assert chart._unobserved_intervals([
        {"time_start_s": "2", "time_end_s": "4"},
        {"time_start_s": "6", "time_end_s": "8"},
    ], 0, 10) == [(0, 2), (4, 6), (8, 10)]
    flow_id = next(iter(chart.context.flows))
    chart.context.by_flow[flow_id] = []
    chart.set_view(flow_id=flow_id, time_window=(0, 10))
    chart.resize(1000, max(chart.minimumHeight(), 300))
    chart.show()
    app.processEvents()
    pixel = chart.grab().toImage().pixelColor(600, 95)
    assert pixel != QColor("white")
    chart.close()
    chart.deleteLater()
    app.processEvents()


def test_timeline_preserves_physical_path_states_across_one_installed_route(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart(read_folder(reference_folder))
    chart.resize(1100, max(chart.minimumHeight(), 320))
    chart.show()
    app.processEvents()
    picture = chart.grab().toImage()
    start, end = chart.time_window
    right = max(176, chart.width() - 28)

    def route_color(moment: float) -> QColor:
        x = round(175 + (right - 175) * (moment - start) / (end - start))
        return picture.pixelColor(x, 87)

    assert route_color(1.7) == QColor("#2D62C6")  # first path works
    assert route_color(5.1) == QColor("#AE7919")  # same installed path is broken
    assert route_color(5.35) == QColor("#B33B50")  # no route is recorded
    assert route_color(6.5) == QColor("#14847C")  # alternate path works
    chart.close()
    chart.deleteLater()
    app.processEvents()


def test_timeline_distinguishes_loop_and_recorded_unknown(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    chart = TimelineChart(read_folder(reference_folder))
    flow_id = next(iter(chart.context.flows))
    route = chart.context.by_flow[flow_id][0]
    chart.resize(1100, max(chart.minimumHeight(), 320))
    chart.show()
    app.processEvents()
    x = round(175 + (chart.width() - 28 - 175) * 1.7 / 11)
    route["route_state"] = "loop"
    chart.update()
    app.processEvents()
    assert chart.grab().toImage().pixelColor(x, 87) == QColor("#9467BD")

    route["route_state"] = "unknown"
    chart.update()
    app.processEvents()
    image = chart.grab().toImage()
    assert any(image.pixelColor(xx, yy) == QColor("#E4C95C")
               for xx in range(x - 8, x + 8) for yy in range(86, 103))
    chart.close()
    chart.deleteLater()
    app.processEvents()


def test_comparison_view_explains_incomparable_conditions_without_ranking() -> None:
    app = QApplication.instance() or QApplication([])
    metrics = {
        key: {
            "symbol": key, "partial_score": {"status": "exact", "value": "50"},
            "contribution": {"status": "exact", "value": "10"},
            "raw": {"status": "exact"},
        }
        for key in gui_module.METRIC_LABELS
    }
    payload = {"integral": {"status": "exact", "value": "50"}, "metrics": metrics}
    first = dict(payload, experiment_id="SYNTHETIC-A")
    second = dict(payload, experiment_id="SYNTHETIC-B")

    incomparable = ComparisonDialog(first, second, ["Состав или исходное состояние сети"])
    labels = [label.text() for label in incomparable.findChildren(QLabel)]
    message = next(label for label in labels if label.startswith("Условия несопоставимы"))
    assert "общую оценку и рейтинг не формируем" in message.lower()
    assert "Состав или исходное состояние сети" in message
    assert "SYNTHETIC-A" in message or any("SYNTHETIC-A" in label for label in labels)
    table = incomparable.findChild(QTableWidget)
    assert table is not None and table.rowCount() == 7
    assert isinstance(incomparable.contribution_chart, gui_module.ComparisonContributionChart)
    assert any("Pзад (P_zad)" in label.text() for label in incomparable.findChildren(QLabel))

    comparable = ComparisonDialog(first, second, [])
    comparable_labels = [label.text() for label in comparable.findChildren(QLabel)]
    assert any(label.startswith("Условия сопоставимы.") for label in comparable_labels)
    incomparable.close()
    comparable.close()
    incomparable.deleteLater()
    comparable.deleteLater()
    app.processEvents()


def test_saved_comparison_offers_k3_without_recalculating(reference_folder: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    second.passport["experiment_id"] = "REF_ROUTING_002"
    for rows in second.tables.values():
        for row in rows:
            row["experiment_id"] = "REF_ROUTING_002"
    second.tables["channels.csv"][0]["tx_routing_bytes"] += 100
    profile = json.loads((ROOT / "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    left = calculate(first, profile).as_dict()
    right = calculate(second, profile).as_dict()
    monkeypatch.setattr("scanner.core.sensitivity.calculate", lambda *_a, **_k: pytest.fail("recalculated"))
    dialog = ComparisonDialog(left, right, [], first_context=(first, profile), second_context=(second, profile))
    analyze = dialog.findChild(QPushButton, "sensitivityAnalyze")
    radius = dialog.findChild(QLineEdit, "sensitivityRadius")
    result = dialog.findChild(QLabel, "sensitivityResult")
    assert analyze.isEnabled()
    analyze.click()
    assert "Преимущество первого сохраняется" in result.text()
    radius.setText("2")
    analyze.click()
    assert "смена предпочтения не доказана" in result.text()
    right["integral"]["status"] = "interval"
    analyze.click()
    assert "К3 недоступен" in result.text()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_series_manager_shows_saved_repeat_statistics_without_recalculation(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    first = read_folder(reference_folder)
    second = copy.deepcopy(first)
    first.passport["repeat_id"] = 1
    second.passport["repeat_id"] = 2
    second.passport["experiment_id"] = "REF_ROUTING_002"
    for rows in second.tables.values():
        for row in rows:
            row["experiment_id"] = "REF_ROUTING_002"
    second.tables["channels.csv"][0]["tx_routing_bytes"] += 100
    profile = json.loads((ROOT / "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    store = DraftStore(tmp_path / "repeats.sqlite3")
    first_id, _, _ = calculate_and_save(store, "first", first, profile)
    second_id, _, _ = calculate_and_save(store, "second", second, profile)
    series_id = store.create_series("Один сценарий")
    store.add_to_series(series_id, first_id)
    store.add_to_series(series_id, second_id)
    manager = SeriesManagerDialog(store)
    assert manager.findChild(QPushButton, "repeatStatisticsAction") is not None
    monkeypatch.setattr("scanner.core.repeats.calculate", lambda *_a, **_k: pytest.fail("recalculated"))
    shown = []
    monkeypatch.setattr(RepeatStatisticsDialog, "exec", lambda dialog: shown.append(dialog.report))
    manager.show_repeat_statistics()
    assert len(shown) == 1
    assert shown[0]["count"] == 2
    assert shown[0]["student_interval_3_56"] is None
    assert shown[0]["independence_basis"] is None
    details = RepeatStatisticsDialog(shown[0])
    table = details.findChild(QTableWidget, "repeatStatisticsTable")
    assert table.rowCount() == 2
    assert any("не доказывают независимость" in label.text() for label in details.findChildren(QLabel))
    details.close()
    details.deleteLater()
    other = copy.deepcopy(second)
    other.passport["experiment_id"] = "REF_ROUTING_003"
    other.passport["repeat_id"] = 3
    other.passport["scenario_id"] = "OTHER_SCENARIO"
    for rows in other.tables.values():
        for row in rows:
            row["experiment_id"] = "REF_ROUTING_003"
    other_id, _, _ = calculate_and_save(store, "other", other, profile)
    store.add_to_series(series_id, other_id)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, _title, message: messages.append(message))
    manager.show_repeat_statistics()
    assert any("scenario_id" in message for message in messages)
    assert len(shown) == 1
    manager.close()
    manager.deleteLater()
    app.processEvents()


def test_comparison_contribution_chart_keeps_interval_bounds_without_midpoint() -> None:
    app = QApplication.instance() or QApplication([])
    metrics = {
        key: {"contribution": {"status": "exact", "lower": "5", "upper": "5", "value": "5"}}
        for key in gui_module.METRIC_LABELS
    }
    metrics["P_zad"]["contribution"] = {
        "status": "interval", "lower": "3", "upper": "8", "value": None,
    }
    chart = gui_module.ComparisonContributionChart([
        {"experiment_id": "A", "metrics": metrics,
         "integral": {"status": "interval", "lower": "33", "upper": "38"}},
    ])
    chart.resize(700, 150)
    image = QImage(chart.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    chart.render(image)
    assert chart.assessments[0]["metrics"]["P_zad"]["contribution"]["lower"] == "3"
    assert chart.assessments[0]["metrics"]["P_zad"]["contribution"]["upper"] == "8"
    colored = sum(1 for x in range(image.width()) for y in range(image.height())
                  if image.pixelColor(x, y) != Qt.GlobalColor.white)
    assert colored > 100
    chart.close()
    chart.deleteLater()
    app.processEvents()


def test_explanation_tab_separates_rebuild_time_and_confirmation_window(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        names = archive.namelist()
        passport_path = next(name for name in names if name.endswith("EXP01_REACTIVE_4/experiment.json"))
        prefix = passport_path.rsplit("/", 1)[0] + "/"
        folder = tmp_path / "adaptivity-explanation"
        folder.mkdir()
        for name in names:
            if name.startswith(prefix) and not name.endswith("/"):
                target = folder / name.removeprefix(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        profile_name = next(name for name in names if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    experiment = read_folder(folder)
    payload = calculate(experiment, profile).as_dict()
    dialog = AssessmentDialog(payload, True, experiment)
    explanation = dialog.findChild(QPlainTextEdit)
    assert explanation is not None
    data = json.loads(explanation.toPlainText())
    assert data["formula_sources"]["P_ad"] == "(3.27)–(3.30)"
    checks = data["metrics"]["P_ad"]["trace"]["checks"]
    assert checks[0]["check_id"] == "F01_AD01"
    assert checks[0]["time_s"] == "0.600000000"
    assert checks[0]["confirmation_window_s"] == "0.2"
    assert dialog.findChild(QLineEdit, "actualEventTime").text() == "4,000"
    assert dialog.findChild(QLineEdit, "newRouteTime").text() == "4,600"
    assert "0,200 с" in dialog.findChild(QLineEdit, "confirmationCheck").text()
    assert any("4,600 − 4,000 = 0,600 с" in label.text()
               for label in dialog.findChildren(QLabel))
    picker = next(combo for combo in dialog.findChildren(QComboBox)
                  if combo.findData("P_zad") >= 0 and combo.findData("P_res") >= 0)
    picker.setCurrentIndex(picker.findData("P_zad"))
    packet_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Окно отправок:" in packet_text
    assert "Отправок: 480" in packet_text
    assert "Первые корректные приёмы в основном окне:" in packet_text
    picker.setCurrentIndex(picker.findData("P_ust"))
    stability_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Назначенных проверок пути:" in stability_text
    assert "пройдена" in stability_text or "не пройдена" in stability_text
    picker.setCurrentIndex(picker.findData("P_res"))
    service_text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Окно счётчиков:" in service_text
    assert "неполных интервалов:" in service_text
    assert any("Демонстрационный профиль Ω" in warning for warning in data["warnings"])
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
        "Эксперименты", "Справка", "О программе", "Выход", "К титульной панели",
    }
    for page, expected in (
        (window.main_menu_page, "Все переходы расположены непосредственно на панели."),
        (window.experiments_page, "Один опыт — один комплект данных"),
        (window.input_page, "Подготовка нового эксперимента"),
        (window.import_preview_page, "Загрузка данных ПАК"),
    ):
        assert any(expected in label.text() for label in page.findChildren(QLabel))
    assert {button.text() for button in window.editor_page.findChildren(QPushButton)}.issuperset({
        "Назад", "Сохранить черновик", "Проверить данные", "Запуск расчёта",
    })
    assert window.editor_heading.text() == "Паспорт: общие сведения и алгоритм"
    window.passport.tabs.setCurrentIndex(2)
    assert window.editor_heading.text() == "Паспорт: узлы сети"
    window.passport.tabs.setCurrentIndex(0)

    def click(page, label: str) -> None:
        button = next(button for button in page.findChildren(QPushButton) if button.text() == label)
        button.click()

    click(window.start_page, "Начать")
    assert window.pages.currentWidget() is window.main_menu_page
    click(window.main_menu_page, "Справка")
    assert window.pages.currentWidget() is window.help_page
    click(window.help_page, "Назад в главное меню")
    click(window.main_menu_page, "О программе")
    assert window.pages.currentWidget() is window.about_page
    click(window.about_page, "Назад в главное меню")
    click(window.main_menu_page, "Эксперименты")
    assert window.pages.currentWidget() is window.experiments_page
    click(window.experiments_page, "Начать новый эксперимент")
    assert window.pages.currentWidget() is window.input_page
    click(window.input_page, "Открыть формы ввода")
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
    assert [editor.tabs.tabText(index) for index in range(editor.tabs.count())] == [
        "Общие сведения", "Среда", "Узлы", "Соединения", "Потоки", "События",
        "Наблюдение", "Проверки", "Полнота",
    ]
    assert editor.value() == original
    experiment_id = editor.findChild(QLineEdit, "passport.experiment_id")
    assert experiment_id is not None
    experiment_id.setText("NEW_EXPERIMENT")
    assert editor.value()["experiment_id"] == "NEW_EXPERIMENT"
    assert original["experiment_id"] != "NEW_EXPERIMENT"
    editor.tabs.setCurrentIndex(2)
    node_id = editor.findChild(QLineEdit, "passport.network.nodes.0.node_id")
    assert node_id is not None
    node_id.setText("RENAMED_NODE")
    assert editor.value()["network"]["nodes"][0]["node_id"] == "RENAMED_NODE"
    nodes = editor.tabs.widget(2).findChild(QTableWidget)
    assert nodes.item(0, 0).text() == "RENAMED_NODE"
    numeric = editor.findChild(QLineEdit, "passport.network.nodes.0.plane_id")
    assert numeric is not None
    numeric.setText("ошибка")
    with pytest.raises(ValueError, match="network.nodes.0.plane_id"):
        editor.value()
    numeric.setText("2")
    assert editor.value()["network"]["nodes"][0]["plane_id"] == 2
    add = next(button for button in editor.tabs.widget(2).findChildren(QPushButton)
               if button.text() == "Добавить строку")
    delete = next(button for button in editor.tabs.widget(2).findChildren(QPushButton)
                  if button.text() == "Удалить")
    count = len(editor.value()["network"]["nodes"])
    add.click()
    assert len(editor.value()["network"]["nodes"]) == count + 1
    delete.click()
    assert len(editor.value()["network"]["nodes"]) == count
    editor.set_value(original)
    assert editor.value() == original
    editor.deleteLater()
    app.processEvents()


def test_passport_fields_are_editable_and_restore_nulls(
    reference_folder: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    original = read_folder(reference_folder).passport
    editor = PassportEditor(original)
    visited: set[str] = set()

    def exercise(root: gui_module.QWidget) -> None:
        for control in root.findChildren(gui_module.QWidget):
            name = control.objectName()
            if not name.startswith("passport.") or name in visited:
                continue
            path = tuple(int(part) if part.isdigit() else part for part in name[9:].split("."))
            before = copy.deepcopy(editor._get(path))
            if isinstance(control, QComboBox):
                current = control.currentIndex()
                control.setCurrentIndex(0 if current else 1)
                assert editor._get(path) != before, name
                control.setCurrentIndex(current)
            elif isinstance(control, (QLineEdit, QPlainTextEdit)):
                kind = editor._kind(str(path[-1]), before)
                if isinstance(before, list):
                    changed = json.dumps([*before, before[0] if before else "AUDIT"], ensure_ascii=False)
                    restored = json.dumps(before, ensure_ascii=False)
                elif kind in {"integer", "number"}:
                    changed = str((before or 0) + 1)
                    restored = "" if before is None else str(before)
                else:
                    changed = str(before or "") + "AUDIT"
                    restored = "" if before is None else str(before)
                setter = control.setText if isinstance(control, QLineEdit) else control.setPlainText
                setter(changed)
                assert editor._get(path) != before, name
                setter(restored)
            else:
                continue
            assert editor._get(path) == before, name
            visited.add(name)

    exercise(editor)
    monkeypatch.setattr(gui_module.QDialog, "exec", lambda dialog: (exercise(dialog), 0)[1])
    editor._open_algorithm_settings()
    for path in (("network", "nodes"), ("network", "links"), ("traffic", "flows"),
                 ("scenario", "events"), ("measurement", "stability", "checks"),
                 ("measurement", "adaptivity", "checks")):
        editor._record_dialog(path, 0, "Поля записи")
    balance = next(button for button in editor.findChildren(QPushButton)
                   if button.text() == "Выбрать каналы для балансировки")
    balance.click()

    def leaf_paths(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from leaf_paths(item, path + (key,))
        elif isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            for item in value:
                yield from leaf_paths(item, path + ("0",))
        else:
            yield ".".join(path)

    covered = {name[9:] for name in visited}
    advanced_or_generated = {
        "format_name", "schema_version", "export_status.logging_gaps",
        "measurement.stability.definition", "measurement.adaptivity.definition",
        "measurement.adaptivity.note_ru",
    }
    missing = {path for path in leaf_paths(original) if path not in covered
               and not path.startswith(("file_contract.", "export_status.tables."))}
    assert missing == advanced_or_generated
    assert editor.value() == original

    blank = gui_module.empty_passport()
    editor.set_value(blank)
    nullable = editor.findChild(QLineEdit, "passport.environment.hardware_description")
    assert nullable is not None
    nullable.setText("Временное описание")
    nullable.clear()
    assert editor.value()["environment"]["hardware_description"] is None
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_passport_summary_cards_and_hidden_record_fields_preserve_input(
    reference_folder: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    original = read_folder(reference_folder).passport
    editor = PassportEditor(original)
    for tab, headings in (
        (2, ("Узел", "Тип", "Плоскость", "Начальное состояние")),
        (3, ("Канал", "Откуда", "Куда", "Доступен")),
        (4, ("Поток", "Источник", "Получатель", "Тип")),
        (5, ("Событие", "Тип", "План, с", "Факт, с", "Результат")),
    ):
        table = editor.tabs.widget(tab).findChild(QTableWidget, "passportSummary")
        assert table is not None
        assert tuple(table.horizontalHeaderItem(i).text() for i in range(table.columnCount())) == headings
        assert table.verticalHeader().isHidden()
        assert table.rowCount() == len({
            2: original["network"]["nodes"], 3: original["network"]["links"],
            4: original["traffic"]["flows"], 5: original["scenario"]["events"],
        }[tab])
    clipboard = QApplication.clipboard()
    previous_clipboard = clipboard.text()
    try:
        clipboard.setText("Узел\tТип\tПлоскость\tНачальное состояние\nN7\tsatellite\t2\tДа")
        paste = next(button for button in editor.tabs.widget(2).findChildren(QPushButton)
                     if button.text() == "Вставить таблицу")
        paste.click()
        assert editor.value()["network"]["nodes"][-1] == {
            "node_id": "N7", "node_type": "satellite", "plane_id": 2, "initial_up": True,
        }
    finally:
        clipboard.setText(previous_clipboard)
    editor.tabs.setCurrentIndex(4)
    details = editor.findChild(QLineEdit, "passport.traffic.flows.0.packetization.payload_bytes")
    assert details is not None
    details.setText("1200")
    assert editor.value()["traffic"]["flows"][0]["packetization"]["payload_bytes"] == 1200

    def edit_full_record(dialog: gui_module.QDialog) -> int:
        interval = dialog.findChild(QLineEdit, "passport.traffic.flows.0.generation.interval_s")
        assert interval is not None
        interval.setText("0.75")
        return 0

    monkeypatch.setattr(gui_module.QDialog, "exec", edit_full_record)
    more = next(button for button in editor.tabs.widget(4).findChildren(QPushButton)
                if button.text() == "Генерация трафика и повторные передачи")
    more.click()
    assert editor.value()["traffic"]["flows"][0]["generation"]["interval_s"] == 0.75
    assert original["traffic"]["flows"][0]["packetization"]["payload_bytes"] != 1200
    editor.deleteLater()
    app.processEvents()


def test_passport_checks_and_completeness_keep_editable_fields(
    reference_folder: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    original = read_folder(reference_folder).passport
    editor = PassportEditor(original)
    checks = editor.tabs.widget(7).findChildren(QTableWidget, "passportChecks")
    assert len(checks) == 2
    assert [table.rowCount() for table in checks] == [
        len(original["measurement"]["stability"]["checks"]),
        len(original["measurement"]["adaptivity"]["checks"]),
    ]

    def edit_check(dialog: gui_module.QDialog) -> int:
        start = dialog.findChild(QLineEdit, "passport.measurement.stability.checks.0.time_start_s")
        assert start is not None
        start.setText("1.25")
        return 0

    monkeypatch.setattr(gui_module.QDialog, "exec", edit_check)
    change = next(button for button in editor.tabs.widget(7).findChildren(QPushButton)
                  if button.text() == "Изменить")
    change.click()
    assert editor.value()["measurement"]["stability"]["checks"][0]["time_start_s"] == 1.25
    summary = editor.tabs.widget(8).findChild(QTableWidget, "passportFormatSummary")
    assert summary is not None and summary.rowCount() == 6
    assert summary.item(4, 1).text() == "40 / 8 / 156"
    complete = editor.tabs.widget(8).findChild(QComboBox, "passport.export_status.complete")
    assert complete is not None
    complete.setCurrentIndex(2)
    assert editor.value()["export_status"]["complete"] is False
    assert editor.value()["file_contract"] == original["file_contract"]

    def edit_additional(dialog: gui_module.QDialog) -> int:
        fields = dialog.findChild(QTableWidget, "passportAdditionalFields")
        assert fields is not None and fields.rowCount() >= 7
        assert fields.item(0, 0).text() == "algorithm"
        fields.setCurrentCell(0, 0)
        button = next(item for item in dialog.findChildren(QPushButton)
                      if item.text() == "Редактировать выбранное поле")
        button.click()
        return 0

    monkeypatch.setattr(gui_module.QDialog, "exec", edit_additional)
    monkeypatch.setattr(gui_module.QInputDialog, "getMultiLineText",
                        lambda *_args: ("Проверено вручную", True))
    button = next(item for item in editor.tabs.widget(8).findChildren(QPushButton)
                  if item.text() == "Все поля формата и дополнительные параметры")
    button.click()
    assert editor.value()["algorithm"]["settings_completeness"] == "Проверено вручную"
    editor.deleteLater()
    app.processEvents()


def test_input_pages_keep_light_background_with_dark_system_palette(
    reference_folder: Path, tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    original_palette = app.palette()
    dark_palette = QPalette(original_palette)
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                 QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button):
        dark_palette.setColor(role, QColor("#111111"))
    app.setPalette(dark_palette)
    window = None
    try:
        window = MainWindow(tmp_path / "dark-palette.sqlite3")
        window._set_experiment(read_folder(reference_folder))
        window.show_editor()
        window.show()
        app.processEvents()

        def assert_no_dark_panel(page: str) -> None:
            image = window.grab().toImage()
            dark_pixels = sum(
                1 for y in range(220, image.height(), 4)
                for x in range(0, image.width(), 4)
                if (color := image.pixelColor(x, y)).red() < 30
                and color.green() < 30 and color.blue() < 30
            )
            assert dark_pixels < 100, f"{page}: {dark_pixels} dark background samples"

        for index in range(window.passport.tabs.count()):
            window.passport.tabs.setCurrentIndex(index)
            app.processEvents()
            assert_no_dark_panel(window.passport.tabs.tabText(index))
        for index in range(1, window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
            assert_no_dark_panel(window.tabs.tabText(index))
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        app.setPalette(original_palette)
        app.processEvents()


def test_algorithm_settings_are_editable_from_passport_dialog(reference_folder: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    editor = PassportEditor(read_folder(reference_folder).passport)
    first_page = editor.tabs.widget(0)
    assert first_page.findChild(QLineEdit, "passport.algorithm.implementation_sha256") is None
    button = first_page.findChild(QPushButton, "algorithmSettingsButton")
    assert button is not None

    def edit_dialog(dialog: gui_module.QDialog) -> int:
        checksum = dialog.findChild(QLineEdit, "passport.algorithm.implementation_sha256")
        assert checksum is not None
        checksum.setText("abc123")
        return 0

    monkeypatch.setattr(gui_module.QDialog, "exec", edit_dialog)
    button.click()
    assert editor.value()["algorithm"]["implementation_sha256"] == "abc123"
    editor.deleteLater()
    app.processEvents()


def test_missing_profile_is_reported_before_calculation(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "missing-profile.sqlite3")
    window._set_experiment(read_folder(reference_folder))
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: messages.append((title, message)))
    window.check()
    assert "PROFILE_ID" in messages[-1][1]
    window.run_assessment()
    assert messages[-1][0] == "Нужен профиль Ω"
    assert "configs/examples/profile_demo.json" in messages[-1][1]
    assert window.tabs.currentWidget() is window.profile
    assert window.assessment_worker is None

    demo_path = ROOT / "configs/examples/profile_demo.json"
    with ZipFile(CONTROL_ARCHIVE) as archive:
        member = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        assert demo_path.read_bytes() == archive.read(member)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(demo_path), "JSON (*.json)"))
    window.import_profile()
    assert validate_profile(window.profile.value()) == []
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_table_editor_supports_add_copy_paste_and_delete() -> None:
    app = QApplication.instance() or QApplication([])
    editor = TableEditor("packets.csv")
    assert {button.text() for button in editor.findChildren(QPushButton)} == {
        "Добавить строку", "Копировать запись", "Удалить строки", "Копировать", "Вставить диапазон",
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


def test_table_editor_duplicates_all_record_fields_without_overwriting_source(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = read_folder(reference_folder).table_strings()["packets.csv"][0]
    editor = TableEditor("packets.csv")
    editor.model.set_rows([source])
    editor.view.setCurrentIndex(editor.model.index(0, editor.model.header.index("packet_id")))
    editor.duplicate_row()
    assert editor.model.rowCount() == 2
    assert editor.model.rows[1] == source
    assert editor.view.currentIndex().row() == 1
    editor.model.setData(editor.model.index(1, editor.model.header.index("packet_id")), "NEW_PACKET")
    assert editor.model.rows[0] == source
    assert editor.model.rows[1]["packet_id"] == "NEW_PACKET"
    assert editor.model.rows[1]["drop_reason"] == source["drop_reason"]
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_table_forms_preserve_csv_fields_and_blank_observations(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = read_folder(reference_folder).table_strings()
    packets = TableEditor("packets.csv")
    packets.model.set_rows(source["packets.csv"][:2])
    assert packets.view.isColumnHidden(packets.model.header.index("drop_reason"))
    assert not packets.view.isColumnHidden(packets.model.header.index("packet_id"))
    packets.view.setCurrentIndex(packets.model.index(0, 2))
    receive = packets.findChild(QLineEdit, "record.packets.csv.rx_time_s")
    assert receive is not None
    receive.clear()
    assert packets.model.rows[0]["rx_time_s"] == ""
    status = packets.findChild(QComboBox, "record.packets.csv.observation_status")
    assert status is not None and status.currentData() == "complete"
    status.setCurrentIndex(status.findData("partial"))
    assert packets.model.rows[0]["observation_status"] == "partial"
    packets.search.setText(source["packets.csv"][1]["packet_id"])
    assert packets.view.isRowHidden(0) and not packets.view.isRowHidden(1)
    assert packets.view.currentIndex().row() == 1
    packets.close()

    channels = TableEditor("channels.csv")
    channels.model.set_rows(source["channels.csv"][:1])
    channels.view.setCurrentIndex(channels.model.index(0, 1))
    up = channels.findChild(QComboBox, "record.channels.csv.link_up")
    assert up is not None
    up.setCurrentIndex(up.findData("0"))
    assert channels.model.rows[0]["link_up"] == "0"
    assert channels.model.rows[0]["queue_mean_bytes"] == source["channels.csv"][0]["queue_mean_bytes"]
    channels.close()

    routes = TableEditor("routes.csv")
    routes.model.set_rows(source["routes.csv"][:1])
    routes.view.setCurrentIndex(routes.model.index(0, 1))
    path = routes.findChild(QPlainTextEdit, "record.routes.csv.path_links")
    assert path is not None and "[" not in path.toPlainText()
    original_links = json.loads(source["routes.csv"][0]["path_links"])
    assert path.toPlainText().splitlines() == original_links
    path.setPlainText("L_A_B\nL_B_C")
    assert json.loads(routes.model.rows[0]["path_links"]) == ["L_A_B", "L_B_C"]
    routes.close()
    app.processEvents()


def test_all_csv_record_fields_round_trip_through_detail_cards(reference_folder: Path) -> None:
    app = QApplication.instance() or QApplication([])
    source = read_folder(reference_folder).table_strings()
    for name in ("packets.csv", "routes.csv", "channels.csv"):
        editor = TableEditor(name)
        editor.model.set_rows(source[name][:1])
        editor.view.setCurrentIndex(editor.model.index(0, 1))
        original = dict(editor.model.rows[0])
        for key in editor.model.header[1:]:
            control_name = f"record.{name}.{key}"
            control = editor.findChild(gui_module.QWidget, control_name)
            assert control is not None, control_name
            if isinstance(control, QComboBox):
                current = control.currentIndex()
                control.setCurrentIndex(0 if current else 1)
                assert editor.model.rows[0][key] != original[key], control_name
                control.setCurrentIndex(current)
            elif isinstance(control, QPlainTextEdit):
                before = control.toPlainText()
                control.setPlainText("AUDIT_LINK")
                assert editor.model.rows[0][key] != original[key], control_name
                control.setPlainText(before)
            else:
                assert isinstance(control, QLineEdit)
                control.setText(original[key] + "AUDIT")
                assert editor.model.rows[0][key] != original[key], control_name
                control.setText(original[key])
            if key == "path_links":
                assert json.loads(editor.model.rows[0][key]) == json.loads(original[key])
            else:
                assert editor.model.rows[0][key] == original[key], control_name
        assert all(editor.model.rows[0][key] == value for key, value in original.items()
                   if key != "path_links")
        editor.close()
        editor.deleteLater()
    app.processEvents()


def test_profile_forms_round_trip_scales_and_edit_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    original = json.loads((ROOT / "configs/examples/profile_demo.json").read_text())
    editor = ProfileEditor(original)
    assert editor.value() == original
    assert editor.scales_table.rowCount() == 7
    assert editor.scales_table.item(0, 0).text() == "Задержка"
    assert "→" in editor.scales_table.item(0, 3).text()
    assert editor.expert_table.rowCount() == 0
    assert "фиксированные веса" in editor.expert_empty_note.text()
    editor.pages.setCurrentIndex(1)
    editor.weights_table.item(0, 0).setText("0,18")
    assert editor.value()["metrics"]["P_zad"]["weight"] == 0.18
    editor.pages.setCurrentIndex(0)
    editor.scales_table.setCurrentCell(0, 0)

    def edit_first_point(dialog: gui_module.QDialog) -> int:
        points = dialog.findChild(QTableWidget, "profilePoints")
        assert points is not None
        points.item(0, 0).setText("0,03")
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        return 0

    monkeypatch.setattr(gui_module.QDialog, "exec", edit_first_point)
    editor.edit_points()
    assert editor.value()["metrics"]["P_zad"]["points"][0][0] == 0.03
    editor.add_expert()
    editor.expert_table.item(0, 0).setText("Источник испытания")
    for column in range(1, 8):
        editor.expert_table.item(0, column).setText("1")
    editor.calculate_weights()
    assert editor.value()["experts"][0]["name_ru"] == "Источник испытания"
    assert abs(sum(editor.value()["metrics"][key]["weight"] for key in gui_module.METRICS) - 1) < 1e-9
    assert validate_profile(editor.value()) == []
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_each_profile_scale_and_weight_is_editable(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    original = json.loads((ROOT / "configs/examples/profile_demo.json").read_text())
    editor = ProfileEditor(original)
    editor.profile_id.setText("AUDIT_PROFILE")
    assert editor.value()["profile_id"] == "AUDIT_PROFILE"
    editor.profile_id.setText(original["profile_id"])
    editor.pages.setCurrentIndex(1)
    for column, key in enumerate(gui_module.METRICS):
        weight = original["metrics"][key]["weight"]
        editor.weights_table.item(0, column).setText("0.001")
        assert editor.value()["metrics"][key]["weight"] == 0.001
        editor.weights_table.item(0, column).setText(str(weight))
        assert editor.value()["metrics"][key]["weight"] == weight
    for control, key in ((editor.profile_status, "profile_status"),
                         (editor.basis, "basis_ru"), (editor.note, "note_ru")):
        setter = control.setText if isinstance(control, QLineEdit) else control.setPlainText
        setter("Аудит")
        assert editor.value()[key] == "Аудит"
        restored = ("Демонстрационный" if key == "profile_status" else original.get(key, ""))
        setter(restored)
        assert editor.value()[key] == original.get(key, "")

    editor.pages.setCurrentIndex(0)
    for row, key in enumerate(gui_module.METRICS):
        editor.scales_table.setCurrentCell(row, 0)
        baseline = original["metrics"][key]["points"][0][0]

        def edit_point(dialog: gui_module.QDialog) -> int:
            points = dialog.findChild(QTableWidget, "profilePoints")
            assert points is not None
            points.item(0, 0).setText(str(float(baseline) + 0.001))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
            return 0

        monkeypatch.setattr(gui_module.QDialog, "exec", edit_point)
        editor.edit_points()
        assert editor.value()["metrics"][key]["points"][0][0] == float(baseline) + 0.001
        editor.set_value(original)
    assert editor.value() == original
    editor.close()
    editor.deleteLater()
    app.processEvents()


def test_import_preview_shows_original_four_file_composition(
    reference_folder: Path, tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "import-preview.sqlite3")
    experiment = read_folder(reference_folder)
    window.show_import_page()
    assert window.pages.currentWidget() is window.import_preview_page
    assert not window.import_open_button.isEnabled()
    assert window.import_files_table.item(0, 2).text() == "Не выбран"
    window.show_import_preview(experiment)
    assert window.pages.currentWidget() is window.import_preview_page
    assert window.import_files_table.rowCount() == 4
    assert window.import_open_button.isEnabled()
    assert window.import_files_table.item(0, 2).text() == "Найден"
    assert window.import_files_table.item(0, 3).text() == "—"
    assert window.import_files_table.item(1, 3).text() == str(len(experiment.tables["packets.csv"]))
    assert "SHA-256" in window.import_files_table.item(0, 0).toolTip()
    window.accept_import_preview()
    assert window.pages.currentWidget() is window.editor_page
    assert window.dirty and len(window.original_files) == 4
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_import_page_keeps_missing_file_visible_without_opening(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    for name in ("experiment.json", "packets.csv", "routes.csv"):
        (incomplete / name).write_bytes((reference_folder / name).read_bytes())
    window = MainWindow(tmp_path / "missing-import.sqlite3")
    errors = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(incomplete))
    monkeypatch.setattr(window, "_error", errors.append)
    window.import_folder()
    assert errors and window.pages.currentWidget() is window.import_preview_page
    assert window.import_path.text() == str(incomplete)
    assert window.import_files_table.item(3, 2).text() == "Отсутствует"
    assert not window.import_open_button.isEnabled()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_closing_nonempty_series_shows_saved_count_and_result_access(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        profile_name = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "series-summary.sqlite3")
    series_id = window.store.create_series("Серия с оценкой")
    assessment_id, _, _ = calculate_and_save(window.store, "series-example", experiment, profile)
    window.store.add_to_series(series_id, assessment_id)
    window.active_series_id = series_id

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    manager = SeriesManagerDialog(window.store, window)
    manager.series_combo.setCurrentIndex(manager.series_combo.findData(series_id))
    manager.close_series()
    assert window.pages.currentWidget() is window.series_complete_page
    assert "сохранено оценок — 1" in window.series_complete_summary.text()

    results = next(button for button in window.series_complete_page.findChildren(QPushButton)
                   if button.text() == "Просмотреть результаты")
    results.click()
    assert window.pages.currentWidget() is window.results_page
    assert window.saved_results.count() == 1
    window.show_series_complete("Серия с оценкой", 1)
    back = next(button for button in window.series_complete_page.findChildren(QPushButton)
                if button.text() == "Вернуться к экспериментам")
    back.click()
    assert window.pages.currentWidget() is window.experiments_page
    window.close()
    window.deleteLater()
    app.processEvents()


def test_failed_series_close_does_not_show_success_page(
    tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    database = tmp_path / "failed-series-close.sqlite3"
    window = MainWindow(database)
    series_id = window.store.create_series("Серия до сбоя")
    window.active_series_id = series_id
    with sqlite3.connect(database) as db:
        db.execute("""
            CREATE TRIGGER reject_series_close BEFORE UPDATE OF status ON series
            BEGIN SELECT RAISE(ABORT, 'simulated series write failure'); END
        """)
    errors = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: errors.append(_args[2]))
    manager = SeriesManagerDialog(window.store, window)
    manager.series_combo.setCurrentIndex(manager.series_combo.findData(series_id))
    manager.close_series()

    assert errors and "simulated series write failure" in errors[0]
    assert window.pages.currentWidget() is not window.series_complete_page
    assert window.active_series_id == series_id
    assert window.store.list_series()[0][2] == "open"
    manager.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_discard_unsaved_changes_restores_last_saved_draft_before_navigation(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "discard-draft.sqlite3")
    revision = window.store.save("saved-draft", experiment, {"profile_id": "", "metrics": {}})
    saved, profile, _ = window.store.load("saved-draft", revision)
    window.draft_id = "saved-draft"
    window._set_experiment(saved, profile)

    edited = window.passport.value()
    edited["experiment_id"] = "UNSAVED-EDIT"
    window.passport.set_value(edited)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )

    window.back_to_experiments()

    assert window.pages.currentWidget() is window.experiments_page
    assert window.passport.value()["experiment_id"] == experiment.experiment_id
    assert window.dirty is False
    assert window.store.list_drafts()[0][:2] == ("saved-draft", revision)
    window.close()
    window.deleteLater()
    app.processEvents()


def test_cancel_unsaved_changes_keeps_form_open_and_unchanged(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "cancel-draft.sqlite3")
    window._set_experiment(experiment)
    window.show_editor()
    edited = window.passport.value()
    edited["experiment_id"] = "UNSAVED-EDIT"
    window.passport.set_value(edited)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )

    window.back_to_experiments()

    assert window.pages.currentWidget() is window.editor_page
    assert window.passport.value()["experiment_id"] == "UNSAVED-EDIT"
    assert window.dirty is True
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_save_unsaved_changes_persists_new_revision_before_navigation(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "save-draft.sqlite3")
    revision = window.store.save("saved-draft", experiment, {"profile_id": "", "metrics": {}})
    saved, profile, _ = window.store.load("saved-draft", revision)
    window.draft_id = "saved-draft"
    window._set_experiment(saved, profile)
    edited = window.passport.value()
    edited["experiment_id"] = "SAVED-EDIT"
    window.passport.set_value(edited)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Save,
    )

    window.back_to_experiments()

    assert window.pages.currentWidget() is window.experiments_page
    current, _, new_revision = window.store.load("saved-draft")
    assert new_revision == revision + 1
    assert current.experiment_id == "SAVED-EDIT"
    assert window.dirty is False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_cancel_then_discard_unsaved_changes_on_application_exit(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "exit-discard.sqlite3")
    revision = window.store.save("exit-draft", experiment, {"profile_id": "", "metrics": {}})
    saved, profile, _ = window.store.load("exit-draft", revision)
    window.draft_id = "exit-draft"
    window._set_experiment(saved, profile)
    edited = window.passport.value()
    edited["experiment_id"] = "UNSAVED-EXIT-EDIT"
    window.passport.set_value(edited)
    window.dirty = True
    choices = iter((QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: next(choices))

    assert window.close() is False
    assert window.dirty is True
    assert window.passport.value()["experiment_id"] == "UNSAVED-EXIT-EDIT"

    assert window.close() is True
    restored, _, restored_revision = window.store.load("exit-draft")
    assert restored_revision == revision
    assert restored.experiment_id == experiment.experiment_id
    assert window.dirty is False
    window.deleteLater()
    app.processEvents()


def test_save_unsaved_changes_on_application_exit_persists_new_revision(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "exit-save.sqlite3")
    revision = window.store.save("exit-save-draft", experiment, {"profile_id": "", "metrics": {}})
    saved, profile, _ = window.store.load("exit-save-draft", revision)
    window.draft_id = "exit-save-draft"
    window._set_experiment(saved, profile)
    edited = window.passport.value()
    edited["experiment_id"] = "SAVED-EXIT-EDIT"
    window.passport.set_value(edited)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Save,
    )

    assert window.close() is True
    persisted, _, persisted_revision = window.store.load("exit-save-draft")
    assert persisted_revision == revision + 1
    assert persisted.experiment_id == "SAVED-EXIT-EDIT"
    assert window.dirty is False
    window.deleteLater()
    app.processEvents()


def test_experiments_menu_routes_new_continue_results_and_main_menu(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        profile_name = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    experiment = read_folder(reference_folder)
    window = MainWindow(tmp_path / "experiments-routes.sqlite3")
    window.store.save("continue-this", experiment, profile)
    result_id, _, _ = calculate_and_save(window.store, "view-this", experiment, profile)
    series_id = window.store.create_series("Existing series")

    def click(page, label: str) -> None:
        button = next(button for button in page.findChildren(QPushButton) if button.text() == label)
        button.click()

    click(window.start_page, "Начать")
    click(window.main_menu_page, "Эксперименты")
    click(window.experiments_page, "Начать новый эксперимент")
    assert window.pages.currentWidget() is window.input_page
    click(window.input_page, "Назад")

    click(window.experiments_page, "Продолжить предыдущий")
    assert window.pages.currentWidget() is window.results_page
    monkeypatch.setattr(
        QInputDialog, "getItem",
        lambda _parent, _title, _label, items, *_args, **_kwargs: (
            next(item for item in items if item.startswith("continue-this")), True,
        ),
    )
    click(window.results_page, "Продолжить черновик")
    assert window.pages.currentWidget() is window.editor_page
    assert window.draft_id == "continue-this"
    window.back_to_experiments()

    click(window.experiments_page, "Просмотреть результаты")
    assert window.pages.currentWidget() is window.results_page
    assert window.saved_results.count() == 1
    assert window.results_table.rowCount() == 1
    assert [window.results_table.horizontalHeaderItem(column).text()
            for column in range(window.results_table.columnCount())] == [
        "Опыт", "Алгоритм", "Сценарий", "Повтор", "Модель", "Состояние", "I(A)", "Сохранено",
    ]
    assert window.results_model.count() == 2
    assert window.results_state.count() == 2
    window.results_model.setCurrentIndex(1)
    assert not window.results_table.isRowHidden(0)
    window.results_model.setCurrentIndex(0)
    window.results_search.setText("несуществующий опыт")
    assert window.results_table.isRowHidden(0)
    assert window.saved_results.currentData() is None
    window.results_search.setText("view-this")
    assert not window.results_table.isRowHidden(0)
    assert window.saved_results.currentData() == result_id

    def add_selected_to_series(dialog: SeriesManagerDialog) -> int:
        labels = [label.text() for label in dialog.findChildren(QLabel)]
        assert any("копия входного набора не является независимым" in text for text in labels)
        dialog.series_combo.setCurrentIndex(dialog.series_combo.findData(series_id))
        dialog.assessment_combo.setCurrentIndex(dialog.assessment_combo.findData(result_id))
        dialog.add_assessment()
        return 0

    monkeypatch.setattr(SeriesManagerDialog, "exec", add_selected_to_series)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)
    click(window.results_page, "Серии")
    assert [row[0] for row in window.store.series_assessments(series_id)] == [result_id]
    click(window.results_page, "Открыть выбранную оценку")
    assert window.pages.currentWidget() is window.assessment_panel
    assert window.assessment_panel.payload["assessment_id"] == result_id
    window.close_assessment()
    assert window.pages.currentWidget() is window.results_page
    click(window.results_page, "Назад")
    click(window.experiments_page, "Назад в главное меню")
    assert window.pages.currentWidget() is window.main_menu_page
    window.close()
    window.deleteLater()
    app.processEvents()


def test_next_experiment_keeps_distinct_inputs_and_results_in_same_series(
    tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        names = archive.namelist()
        experiment_paths = [name for name in names if name.endswith("/experiment.json")
                            and "/experiments/" in name]
        profile_name = next(name for name in names if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
        experiments = []
        for passport_path in experiment_paths[:2]:
            prefix = passport_path.rsplit("/", 1)[0] + "/"
            folder = tmp_path / passport_path.split("/")[-2]
            folder.mkdir()
            for name in names:
                if name.startswith(prefix):
                    relative = name.removeprefix(prefix)
                    if relative and not name.endswith("/"):
                        target = folder / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(archive.read(name))
            experiments.append(read_folder(folder))

    first, second = experiments
    assert first.experiment_id != second.experiment_id
    window = MainWindow(tmp_path / "next-experiment.sqlite3")
    series_id = window.store.create_series("Два синтетических опыта")
    window.active_series_id = series_id
    progress_messages: list[str] = []
    start_worker = AssessmentWorker.start

    def start_and_observe(worker: AssessmentWorker) -> None:
        worker.progress.connect(progress_messages.append)
        start_worker(worker)

    monkeypatch.setattr(AssessmentWorker, "start", start_and_observe)
    run_calculation = gui_module.calculate_and_save

    def slow_calculation(*args, progress=None, **kwargs):
        def report(message: str) -> None:
            if progress is not None:
                progress(message)
            time.sleep(0.015)

        return run_calculation(*args, progress=report, **kwargs)

    monkeypatch.setattr(gui_module, "calculate_and_save", slow_calculation)
    progress_labels: list[str] = []

    def run_and_wait(experiment) -> None:
        window._set_experiment(experiment, profile)
        window.run_assessment()
        worker = window.assessment_worker
        assert worker is not None
        deadline = time.monotonic() + 10
        while window.assessment_worker is not None and time.monotonic() < deadline:
            app.processEvents()
            if window.progress_dialog is not None:
                progress_labels.append(window.progress_dialog.labelText())
            time.sleep(0.01)
        app.processEvents()
        assert window.assessment_worker is None
        assert window.assessment_panel is not None
        assert window.pages.currentWidget() is window.assessment_panel
        assert window.assessment_panel.payload["assessment_id"]
        labels = [label.text() for label in window.assessment_panel.findChildren(QLabel)]
        assert any("Демонстрационный профиль Ω" in label for label in labels)

    run_and_wait(first)
    first_id = window.assessment_panel.payload["assessment_id"]
    window.close_assessment()
    next_button = next(button for button in window.editor_page.findChildren(QPushButton)
                       if button.text() == "Добавить следующий эксперимент")
    next_button.click()
    assert window.pages.currentWidget() is window.input_page
    assert window.active_series_id == series_id

    empty_forms = next(button for button in window.input_page.findChildren(QPushButton)
                       if button.text() == "Открыть формы ввода")
    empty_forms.click()
    assert window.draft_id is None
    assert all(editor.model.rowCount() == 0 for editor in window.tables.values())
    run_and_wait(second)
    second_id = window.assessment_panel.payload["assessment_id"]

    assert first_id != second_id
    entries = window.store.series_assessments(series_id)
    assert [entry[0] for entry in entries] == [first_id, second_id]
    comparison_table = window.assessment_panel.findChild(QTableWidget, "comparisonSummary")
    assert comparison_table is not None and comparison_table.rowCount() == 2
    assert comparison_table.item(0, 0).text() == first.experiment_id
    assert comparison_table.item(1, 0).text() == second.experiment_id
    assert len(window.assessment_panel.series_contribution_chart.assessments) == 2
    saved_first, experiment_first, _ = window.store.load_assessment(first_id)
    saved_second, experiment_second, _ = window.store.load_assessment(second_id)
    assert experiment_first.experiment_id == saved_first["experiment_id"] == first.experiment_id
    assert experiment_second.experiment_id == saved_second["experiment_id"] == second.experiment_id
    assert len(experiment_first.tables["packets.csv"]) == len(first.tables["packets.csv"])
    assert len(experiment_second.tables["packets.csv"]) == len(second.tables["packets.csv"])
    assert all(row["experiment_id"] == first.experiment_id
               for row in experiment_first.tables["packets.csv"])
    assert all(row["experiment_id"] == second.experiment_id
               for row in experiment_second.tables["packets.csv"])
    assert {"Подготовка модели К0", "Расчёт показателей К1", "Оценивание К2"}.issubset(progress_messages)
    assert {"Подготовка модели К0", "Расчёт показателей К1", "Оценивание К2"}.issubset(set(progress_labels))
    window.close()
    window.deleteLater()
    app.processEvents()


def test_gui_validation_error_returns_to_editor_without_saving_result(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "validation-error.sqlite3")
    window._set_experiment(read_folder(reference_folder), {})
    window.show_editor()
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: errors.append(str(message)))

    window.run_assessment()
    assert window.assessment_worker is None
    assert window.pages.currentWidget() is window.editor_page
    assert window.store.list_assessments() == []
    assert errors and "profile_id" in errors[0]
    window.close()
    window.deleteLater()
    app.processEvents()


def test_gui_result_write_retry_reuses_calculated_payload(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    with ZipFile(CONTROL_ARCHIVE) as archive:
        profile_name = next(name for name in archive.namelist() if name.endswith("support/profile_demo.json"))
        profile = json.loads(archive.read(profile_name))
    window = MainWindow(tmp_path / "gui-retry.sqlite3")
    window._set_experiment(read_folder(reference_folder), profile)
    window.show_editor()
    original_calculate_and_save = __import__("scanner.gui", fromlist=["calculate_and_save"]).calculate_and_save
    calculations = 0

    def count_calculation(*args, **kwargs):
        nonlocal calculations
        calculations += 1
        return original_calculate_and_save(*args, **kwargs)

    monkeypatch.setattr("scanner.gui.calculate_and_save", count_calculation)
    original_save = window.store.save_assessment
    writes = 0

    def fail_first_write(*args, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 1:
            raise OSError("temporary test storage failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(window.store, "save_assessment", fail_first_write)
    monkeypatch.setattr(
        QMessageBox, "critical",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Close,
    )

    window.run_assessment()
    worker = window.assessment_worker
    assert worker is not None and worker.wait(10_000)
    deadline = time.monotonic() + 5
    while window.assessment_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()

    assert window.unsaved_assessment is not None
    assert window.retry_button.isEnabled()
    assert window.pages.currentWidget() is window.assessment_panel
    assert calculations == 1 and writes == 1
    window.retry_assessment_save()
    assert window.unsaved_assessment is None
    assert calculations == 1 and writes == 2
    assert len(window.store.list_assessments()) == 1
    window.close()
    window.deleteLater()
    app.processEvents()
