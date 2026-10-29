from __future__ import annotations

import os
import json
from copy import deepcopy
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from scanner.gui import MainWindow
from scanner.io.format import read_folder


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


def test_incomplete_row_survives_save_and_reopen(tmp_path: Path) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path / "drafts.sqlite3")
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
