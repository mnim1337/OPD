from __future__ import annotations

import csv
import copy
import hashlib
import io
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
    with pytest.raises(DataError, match="FORMAT_VERSION") as wrong_version:
        read_folder(reference_folder)
    assert wrong_version.value.diagnostic.location == "experiment.json"
    assert wrong_version.value.diagnostic.field == "schema_version"
    assert wrong_version.value.diagnostic.remedy
    passport_path.write_bytes(original)
    packet_path = reference_folder / "packets.csv"
    packet_path.write_bytes(packet_path.read_bytes().replace(b"flow_id", b"flow_identifier", 1))
    with pytest.raises(DataError, match="FORMAT_HEADER") as wrong_header:
        read_folder(reference_folder)
    assert wrong_header.value.diagnostic.location == "packets.csv"
    assert wrong_header.value.diagnostic.remedy


def _change_csv_cell(folder: Path, name: str, column: str, value: str) -> None:
    path = folder / name
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0][column] = value
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS[name], lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    raw = output.getvalue().encode("utf-8")
    path.write_bytes(raw)
    passport_path = folder / "experiment.json"
    passport = json.loads(passport_path.read_text(encoding="utf-8"))
    metadata = passport["export_status"]["tables"][name]
    metadata["size_bytes"] = len(raw)
    metadata["sha256"] = hashlib.sha256(raw).hexdigest()
    passport_path.write_text(json.dumps(passport, ensure_ascii=False) + "\n", encoding="utf-8")


@pytest.mark.parametrize(("case", "expected_code", "expected_field"), [
    ("malformed_csv", "FORMAT_ROW", ""),
    ("negative_size", "TYPE_INT", "payload_bytes"),
    ("foreign_flow", "REFERENCE_FLOW", "flow_id"),
    ("foreign_link", "REFERENCE_PATH", "path_links"),
])
def test_at06_import_rejects_bad_values_with_addressed_diagnostics(
    reference_folder: Path, case: str, expected_code: str, expected_field: str,
) -> None:
    if case == "malformed_csv":
        path = reference_folder / "packets.csv"
        rows = path.read_bytes().split(b"\r\n")
        rows[1] += b",unexpected"
        path.write_bytes(b"\r\n".join(rows))
    elif case == "negative_size":
        _change_csv_cell(reference_folder, "packets.csv", "payload_bytes", "-1")
    elif case == "foreign_flow":
        _change_csv_cell(reference_folder, "packets.csv", "flow_id", "FOREIGN_FLOW")
    else:
        path = reference_folder / "routes.csv"
        with path.open(encoding="utf-8", newline="") as stream:
            first = next(csv.DictReader(stream))
        links = json.loads(first["path_links"])
        links[0] = "FOREIGN_LINK"
        _change_csv_cell(reference_folder, "routes.csv", "path_links", json.dumps(links))
    with pytest.raises(DataError) as caught:
        read_folder(reference_folder)
    diagnostic = caught.value.diagnostic
    assert diagnostic.code == expected_code
    assert diagnostic.location.endswith(":2")
    assert diagnostic.field == expected_field
    assert diagnostic.remedy


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


def test_observed_failure_loss_and_no_route_are_valid_input(reference_folder: Path) -> None:
    imported = read_folder(reference_folder)
    assert any(not row["link_up"] for row in imported.tables["channels.csv"])
    assert any(row["route_state"] == "no_route" for row in imported.tables["routes.csv"])
    assert any(row["drop_reason"] == "no_route" for row in imported.tables["packets.csv"])
    assert not [item for item in validate_experiment(imported) if item.severity == "error"]


def test_export_refuses_incomplete_experiment(reference_folder: Path, tmp_path: Path) -> None:
    imported = read_folder(reference_folder)
    imported.tables["routes.csv"].clear()
    destination = tmp_path / "refused"
    with pytest.raises(DataError, match="TIME_COVERAGE"):
        export_four(imported, destination)
    assert not destination.exists()


@pytest.mark.parametrize(("field", "value"), [
    ("json_line_ending", "CRLF"),
    ("csv_boolean_values", ["true", "false"]),
    ("identifier_pattern", "anything"),
])
def test_file_contract_is_checked_for_import_and_manual_input(reference_folder: Path, field: str, value: object) -> None:
    imported = read_folder(reference_folder)
    passport = copy.deepcopy(imported.passport)
    passport["file_contract"][field] = value
    manual = from_manual(passport, imported.table_strings())
    manual_errors = [item for item in validate_experiment(manual, complete=False) if item.severity == "error"]
    assert any(item.code == "FORMAT_CONTRACT" and item.location == "file_contract" and item.field == field for item in manual_errors)

    path = reference_folder / "experiment.json"
    path.write_text(json.dumps(passport, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(DataError) as caught:
        read_folder(reference_folder)
    assert caught.value.diagnostic.code == "FORMAT_CONTRACT"
    assert caught.value.diagnostic.location == "file_contract"
    assert caught.value.diagnostic.field == field


@pytest.mark.parametrize(("mutation", "code", "field"), [
    ("duplicate_path", "REFERENCE_DUP", "path_id"),
    ("unknown_share", "FIELD_REQUIRED", "selection_share"),
])
def test_multipath_set_rejects_invalid_rows_for_both_inputs(
    reference_folder: Path, mutation: str, code: str, field: str,
) -> None:
    imported = read_folder(reference_folder)
    passport = copy.deepcopy(imported.passport)
    passport["algorithm"]["routing_mode"] = "multipath_per_flow"
    rows = imported.table_strings()
    first = rows["routes.csv"][0]
    assert first["route_state"] == "installed"
    first["selection_share"] = "0.5"
    second = dict(first)
    if mutation == "duplicate_path":
        second["selection_share"] = "0.5"
    else:
        second["path_id"] = "OTHER_PATH"
        second["selection_share"] = ""
    rows["routes.csv"].insert(1, second)
    manual = from_manual(passport, rows)
    errors = [item for item in validate_experiment(manual, complete=False) if item.severity == "error"]
    assert any(item.code == code and item.field == field and item.location.startswith("routes.csv:") for item in errors)

    route_path = reference_folder / "routes.csv"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=HEADERS["routes.csv"], lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows["routes.csv"])
    raw = output.getvalue().encode("utf-8")
    route_path.write_bytes(raw)
    passport["export_status"]["tables"]["routes.csv"].update({
        "row_count": len(rows["routes.csv"]), "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    })
    (reference_folder / "experiment.json").write_text(json.dumps(passport, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(DataError) as caught:
        read_folder(reference_folder)
    assert caught.value.diagnostic.code == code
    assert caught.value.diagnostic.field == field
    assert caught.value.diagnostic.location.startswith("routes.csv:")


def test_impossible_packet_time_is_rejected_but_recorded_loop_is_valid(reference_folder: Path) -> None:
    imported = read_folder(reference_folder)
    packet = imported.tables["packets.csv"][0]
    packet["observed_until_s"] = packet["tx_time_s"] - 1
    errors = [item for item in validate_experiment(imported, complete=False) if item.severity == "error"]
    assert any(item.code == "TIME_PACKET" and item.location == "packets.csv:2" and item.field == "observed_until_s" for item in errors)

    imported = read_folder(reference_folder)
    route = imported.tables["routes.csv"][0]
    route["route_state"] = "loop"
    route["path_id"] = "LOOP_PATH"
    route["path_links"] = ["L_G01_S01", "L_S01_S02", "L_S02_S01"]
    assert not [item for item in validate_experiment(imported, complete=False) if item.severity == "error"]


def test_invalid_event_change_has_diagnostic_for_both_inputs(reference_folder: Path) -> None:
    imported = read_folder(reference_folder)
    passport = copy.deepcopy(imported.passport)
    passport["scenario"]["events"][0]["changes"]["capacity_bps"] = "invalid"
    manual = from_manual(passport, imported.table_strings())
    errors = [item for item in validate_experiment(manual, complete=False) if item.severity == "error"]
    assert any(item.code == "TYPE_NUMBER" and item.location == "scenario.events[1]" and item.field == "changes.capacity_bps" for item in errors)
    (reference_folder / "experiment.json").write_text(json.dumps(passport, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(DataError) as caught:
        read_folder(reference_folder)
    assert caught.value.diagnostic.code == "TYPE_NUMBER"
    assert caught.value.diagnostic.location == "scenario.events[1]"
    assert caught.value.diagnostic.field == "changes.capacity_bps"
