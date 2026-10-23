from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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
