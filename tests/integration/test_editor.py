from __future__ import annotations

import os
import json
import hashlib
import time
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from scanner.core import calculate
from scanner.gui import MainWindow
from scanner.io.format import export_four, read_folder
from scanner.io.validation import validate_experiment


def test_editor_keeps_all_fields_and_table_rows(reference_folder: Path, tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "drafts.sqlite3")
    original = read_folder(reference_folder)
    window._set_experiment(original)
    edited, _ = window._experiment()
    assert edited.passport == original.passport
    assert edited.tables == original.tables
    assert edited.source == "import"
    window.tables["packets.csv"].model.setData(window.tables["packets.csv"].model.index(0, 4), "")
    edited, _ = window._experiment()
    assert edited.tables["packets.csv"][0]["rx_time_s"] is None
    assert edited.source == "edited_import"
    window.dirty = False
    window.close()


def test_manual_forms_match_import_after_export_and_calculation(
    reference_folder: Path, tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    imported = read_folder(reference_folder)
    profile = json.loads((Path(__file__).resolve().parents[2] /
                          "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    window = MainWindow(tmp_path / "manual.sqlite3")
    window.passport.set_value(deepcopy(imported.passport))
    window.profile.set_value(deepcopy(profile))
    source_rows = imported.table_strings()
    for name, editor in window.tables.items():
        editor.model.set_rows(source_rows[name])
    window.tables["packets.csv"].model.rows[0]["experiment_id"] = "STALE"
    manual, selected_profile = window._experiment()
    assert manual.source == "manual"
    assert manual.passport == imported.passport
    assert manual.tables == imported.tables
    assert any(row["rx_time_s"] is None for row in manual.tables["packets.csv"])
    assert all(row["queue_mean_bytes"] is None for row in manual.tables["channels.csv"])
    assert selected_profile == profile
    assert not [item for item in validate_experiment(manual, complete=True)
                if item.severity == "error"]

    manual_result = calculate(manual, selected_profile)
    imported_result = calculate(imported, profile)
    assert manual_result.metrics == imported_result.metrics
    assert manual_result.groups == imported_result.groups
    assert manual_result.integral == imported_result.integral

    reidentified = deepcopy(manual)
    reidentified.passport["experiment_id"] = "MANUAL_OTHER"
    for rows in reidentified.tables.values():
        for row in rows:
            row["experiment_id"] = "MANUAL_OTHER"
    assert calculate(reidentified, profile).metrics == imported_result.metrics

    exported_folder = tmp_path / "manual-export"
    export_four(manual, exported_folder)
    reimported = read_folder(exported_folder)
    export_status = reimported.passport["export_status"]["tables"]
    for name, rows in manual.tables.items():
        data = (exported_folder / name).read_bytes()
        assert export_status[name] == {
            "row_count": len(rows), "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    assert reimported.tables == imported.tables
    reimported_result = calculate(reimported, profile)
    assert reimported_result.metrics == imported_result.metrics
    assert reimported_result.integral == imported_result.integral
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()


def test_manual_draft_reopens_and_calculates_without_source_files(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    imported = read_folder(reference_folder)
    profile = json.loads((Path(__file__).resolve().parents[2] /
                          "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    expected = calculate(imported, profile)
    database = tmp_path / "manual-cycle.sqlite3"
    window = MainWindow(database)
    window.passport.set_value(deepcopy(imported.passport))
    window.profile.set_value(deepcopy(profile))
    for name, editor in window.tables.items():
        editor.model.set_rows(imported.table_strings()[name])
    assert not window.original_files
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("manual-cycle", True))
    assert window.save_draft()
    window.close()
    window.deleteLater()
    app.processEvents()

    for path in reference_folder.iterdir():
        path.unlink()
    reference_folder.rmdir()
    reopened = MainWindow(database)
    monkeypatch.setattr(QInputDialog, "getItem", lambda _parent, _title, _prompt, labels, *_args: (
        labels[0], True,
    ))
    reopened.open_draft()
    assert reopened.source == "manual"
    assert reopened.profile.value() == profile
    restored, _ = reopened._experiment()
    assert restored.tables == imported.tables
    reopened.run_assessment()
    deadline = time.monotonic() + 10
    while reopened.assessment_worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert reopened.assessment_worker is None
    assert reopened.assessment_panel is not None
    payload = reopened.assessment_panel.payload
    assert payload["assessment_id"]
    assert payload["metrics"] == expected.as_dict()["metrics"]
    assert payload["integral"] == expected.as_dict()["integral"]
    saved_payload, saved_input, saved_profile = reopened.store.load_assessment(payload["assessment_id"])
    assert saved_payload["metrics"] == payload["metrics"]
    assert saved_input.tables == imported.tables
    assert saved_profile == profile
    reopened.close()
    reopened.deleteLater()
    app.processEvents()


def test_incomplete_row_survives_save_and_reopen(tmp_path: Path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    database = tmp_path / "drafts.sqlite3"
    window = MainWindow(database)
    window.draft_id = "unfinished"
    packets = window.tables["packets.csv"].model
    packets.insertRows(0, 1)
    packets.setData(packets.index(0, 1), "F01")
    packets.setData(packets.index(0, 3), "not-yet-a-time")
    assert window.save_draft()
    passport, tables, _, _, _, revision = window.store.load_raw("unfinished")
    assert revision == 1
    assert tables["packets.csv"][0]["tx_time_s"] == "not-yet-a-time"
    assert tables["packets.csv"][0]["payload_bytes"] == ""
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()

    restarted = MainWindow(database)
    # Select the actual UI label, including the saved timestamp.
    monkeypatch.setattr(QInputDialog, "getItem", lambda _parent, _title, _prompt, labels, *_args: (
        labels[0], True,
    ))
    restarted.open_draft()
    assert restarted.draft_id == "unfinished"
    assert restarted.tables["packets.csv"].model.rows[0]["tx_time_s"] == "not-yet-a-time"
    assert restarted.tables["packets.csv"].model.rows[0]["payload_bytes"] == ""
    assert restarted.pages.currentWidget() is restarted.editor_page
    restarted.close()
    restarted.deleteLater()
    app.processEvents()


def test_reimport_opens_saved_revision_or_creates_linked_copy(
    reference_folder: Path, tmp_path: Path, monkeypatch,
) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "duplicate.sqlite3")
    original = read_folder(reference_folder)
    profile = json.loads((Path(__file__).resolve().parents[2] /
                          "configs/examples/profile_demo.json").read_text(encoding="utf-8"))
    revision = window.store.save("original", original, profile)
    assert window.store.matching_import(original) == ("original", revision)

    window.show_import_preview(original)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel)
    window.accept_import_preview()
    assert window.pages.currentWidget() is window.import_preview_page
    assert window.pending_import is original

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    window.accept_import_preview()
    assert window.draft_id == "original"
    assert window.dirty is False
    assert window.profile.value() == profile
    assert len(window.store.list_drafts()) == 1

    window.show_import_preview(original)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No)
    window.accept_import_preview()
    copy_id = window.draft_id
    assert copy_id != "original"
    assert window.store.copy_origin(copy_id) == ("original", revision)
    assert window.store.load(copy_id)[0].original_files == original.original_files
    assert len(window.store.list_drafts()) == 2
    backup = tmp_path / "duplicate-backup.sqlite3"
    window.store.backup(backup)
    window.store.restore(backup)
    assert window.store.copy_origin(copy_id) == ("original", revision)

    changed = deepcopy(original)
    changed.original_files["packets.csv"] += b"\n"
    assert window.store.matching_import(changed) is None
    different_id = deepcopy(original)
    different_id.passport["experiment_id"] = "OTHER"
    assert window.store.matching_import(different_id) is None
    window.close()
    window.deleteLater()
    app.processEvents()
