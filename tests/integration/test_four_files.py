from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from scanner.io.format import DataError, export_four, from_manual, read_folder
from scanner.io.model import HEADERS
from scanner.io.storage import DraftStore
from scanner.io.validation import validate_experiment

FIVE = Path(__file__).resolve().parents[2] / "requirements/эксперимент 5 штук/PAK_5_experiments_20_files.zip"


def test_reference_round_trip_and_manual_equivalence(reference_folder: Path, tmp_path: Path) -> None:
    imported = read_folder(reference_folder)
    manual = from_manual(json.loads(json.dumps(imported.passport)), imported.table_strings())
    assert manual.tables == imported.tables
    exported = tmp_path / "exported"
    export_four(manual, exported)
    restored = read_folder(exported)
    assert restored.tables == imported.tables
    assert restored.passport["data_origin"] == "manual_prepared"
    for name in HEADERS:
        assert restored.passport["export_status"]["tables"][name]["row_count"] == len(imported.tables[name])


def test_five_synthetic_inputs_import(tmp_path: Path) -> None:
    with ZipFile(FIVE) as archive:
        groups: dict[str, list[str]] = {}
        for name in archive.namelist():
            if name.endswith((".json", ".csv")):
                groups.setdefault(str(Path(name).parent), []).append(name)
        accepted = []
        for index, names in enumerate(groups.values()):
            if len(names) != 4:
                continue
            folder = tmp_path / str(index)
            folder.mkdir()
            for name in names:
                (folder / Path(name).name).write_bytes(archive.read(name))
            imported = read_folder(folder)
            exported = tmp_path / f"exported_{index}"
            export_four(imported, exported)
            restored = read_folder(exported)
            assert restored.tables == imported.tables
            accepted.append(imported.experiment_id)
    assert len(accepted) == 5
    assert len(set(accepted)) == 5


def test_draft_revisions_preserve_original_and_incomplete_data(reference_folder: Path, tmp_path: Path) -> None:
    imported = read_folder(reference_folder)
    store = DraftStore(tmp_path / "drafts.sqlite3")
    assert store.save("work", imported) == 1
    imported.tables["packets.csv"][0]["rx_time_s"] = None
    imported.passport["description_ru"] = "редакция"
    assert store.save("work", imported) == 2
    first, _, revision = store.load("work", 1)
    second, _, _ = store.load("work")
    assert revision == 1
    assert first.tables["packets.csv"][0]["rx_time_s"] is not None
    assert second.tables["packets.csv"][0]["rx_time_s"] is None
    assert first.original_files["experiment.json"] == (reference_folder / "experiment.json").read_bytes()
    assert second.passport["description_ru"] == "редакция"


def test_profile_versions_are_independent_of_draft_revisions(reference_folder: Path, tmp_path: Path) -> None:
    imported = read_folder(reference_folder)
    store = DraftStore(tmp_path / "drafts.sqlite3")
    profile = {"profile_id": "PROFILE_A", "metrics": {}}
    assert store.save("work", imported, profile) == 1
    assert store.save("work", imported, profile) == 2
    profile["note_ru"] = "Новые основания"
    assert store.save("work", imported, profile) == 3
    assert store.profile_version("work", 1) == ("PROFILE_A", 1)
    assert store.profile_version("work", 2) == ("PROFILE_A", 1)
    assert store.profile_version("work", 3) == ("PROFILE_A", 2)


def test_import_rejects_unknown_version_and_wrong_csv_header(reference_folder: Path) -> None:
    passport_path = reference_folder / "experiment.json"
    original = passport_path.read_bytes()
    passport = json.loads(original)
    passport["schema_version"] = "2.0.0"
    passport_path.write_text(json.dumps(passport, ensure_ascii=False) + "\n")
    with pytest.raises(DataError, match="FORMAT_VERSION"):
        read_folder(reference_folder)
    passport_path.write_bytes(original)
    packet_path = reference_folder / "packets.csv"
    packet_path.write_bytes(packet_path.read_bytes().replace(b"flow_id", b"flow_identifier", 1))
    with pytest.raises(DataError, match="FORMAT_HEADER"):
        read_folder(reference_folder)


def test_malformed_passport_object_has_diagnostic(reference_folder: Path) -> None:
    path = reference_folder / "experiment.json"
    passport = json.loads(path.read_text())
    passport["scenario"]["events"][0]["changes"] = []
    path.write_text(json.dumps(passport, ensure_ascii=False) + "\n")
    with pytest.raises(DataError, match="TYPE_OBJECT"):
        read_folder(reference_folder)


def test_validation_addresses_broken_reference_and_keeps_empty_distinct(reference_folder: Path) -> None:
    imported = read_folder(reference_folder)
    imported.tables["packets.csv"][0]["flow_id"] = "MISSING_FLOW"
    imported.tables["packets.csv"][0]["rx_time_s"] = None
    report = validate_experiment(imported, complete=False)
    assert any(item.code == "REFERENCE_FLOW" and item.location == "packets.csv:2" for item in report)
    assert imported.tables["packets.csv"][0]["rx_time_s"] is None


def test_export_refuses_incomplete_experiment(reference_folder: Path, tmp_path: Path) -> None:
    imported = read_folder(reference_folder)
    imported.tables["routes.csv"].clear()
    destination = tmp_path / "refused"
    with pytest.raises(DataError, match="TIME_COVERAGE"):
        export_four(imported, destination)
    assert not destination.exists()
