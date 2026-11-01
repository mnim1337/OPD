"""Four-file PAK_ROUTING_EXPORT_4F 1.0.0 reader and writer."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .model import Diagnostic, Experiment, FILES, HEADERS, NUMERIC, REQUIRED_NUMERIC, file_contract
from .validation import validate_experiment

NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")


class DataError(ValueError):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(str(diagnostic))
        self.diagnostic = diagnostic


def _fail(code: str, location: str, field: str, message: str, remedy: str) -> None:
    raise DataError(Diagnostic("error", code, location, field, message, remedy))


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"повтор ключа {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise ValueError(f"недопустимая константа {value}")


def parse_passport(data: bytes) -> dict[str, Any]:
    if data.startswith(b"\xef\xbb\xbf"):
        _fail("FORMAT_BOM", "experiment.json", "", "BOM запрещён", "Сохраните UTF-8 без BOM")
    try:
        text = data.decode("utf-8")
        if "\r" in text:
            raise ValueError("ожидаются окончания строк LF")
        result = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_bad_constant)
    except (UnicodeError, ValueError) as exc:
        _fail("FORMAT_JSON", "experiment.json", "", str(exc), "Исправьте JSON и кодировку")
    if not isinstance(result, dict):
        _fail("TYPE_JSON", "experiment.json", "", "нужен объект", "Укажите объект паспорта")
    return result


def _parse_cell(name: str, line: int, key: str, value: str) -> Any:
    kind = NUMERIC[name].get(key)
    if kind is None:
        return value
    if value == "" and key not in REQUIRED_NUMERIC[name]:
        return None
    location = f"{name}:{line}"
    if kind == "path":
        try:
            path = json.loads(value, object_pairs_hook=_unique_keys, parse_constant=_bad_constant)
        except ValueError as exc:
            _fail("TYPE_PATH", location, key, str(exc), "Укажите JSON-массив link_id")
        if not isinstance(path, list) or not all(isinstance(item, str) for item in path):
            _fail("TYPE_PATH", location, key, "нужен массив строк", "Укажите JSON-массив link_id")
        return path
    if kind == "boolean":
        if value not in {"0", "1"}:
            _fail("TYPE_BOOL", location, key, "ожидается 0 или 1", "Исправьте признак состояния")
        return value == "1"
    if kind == "integer":
        if not INTEGER.fullmatch(value):
            _fail("TYPE_INT", location, key, "ожидается целое неотрицательное число", "Исправьте число")
        return int(value)
    if not NUMBER.fullmatch(value):
        _fail("TYPE_DECIMAL", location, key, "ожидается конечное число с десятичной точкой", "Исправьте число")
    try:
        return Decimal(value)
    except InvalidOperation:
        _fail("TYPE_DECIMAL", location, key, "некорректное число", "Исправьте число")


def normalize_rows(name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if name not in HEADERS:
        raise ValueError(f"Неизвестная таблица {name}")
    result = []
    for line, row in enumerate(rows, 2):
        if set(row) != set(HEADERS[name]):
            _fail("FORMAT_HEADER", f"{name}:{line}", "", "набор полей строки не совпадает со схемой", "Используйте точные заголовки 1.0.0")
        result.append({key: _parse_cell(name, line, key, str(row[key]) if row[key] is not None else "") for key in HEADERS[name]})
    return result


def parse_csv(name: str, data: bytes) -> list[dict[str, Any]]:
    if data.startswith(b"\xef\xbb\xbf"):
        _fail("FORMAT_BOM", name, "", "BOM запрещён", "Сохраните UTF-8 без BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        _fail("FORMAT_UTF8", name, "", str(exc), "Сохраните UTF-8")
    if not text.endswith("\r\n") or text.replace("\r\n", "").find("\n") >= 0 or text.replace("\r\n", "").find("\r") >= 0:
        _fail("FORMAT_EOL", name, "", "ожидаются строки CRLF", "Сохраните CSV с CRLF")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader)
        if tuple(header) != HEADERS[name]:
            _fail("FORMAT_HEADER", name, "", "заголовок или порядок полей неверен", "Используйте схему 1.0.0")
        rows = []
        for line, values in enumerate(reader, 2):
            if len(values) != len(header):
                _fail("FORMAT_ROW", f"{name}:{line}", "", "число ячеек не совпадает с заголовком", "Исправьте строку")
            if any("\r" in value or "\n" in value for value in values):
                _fail("FORMAT_ROW", f"{name}:{line}", "", "перенос строки внутри ячейки запрещён", "Уберите перенос строки")
            rows.append(dict(zip(header, values, strict=True)))
    except (csv.Error, StopIteration) as exc:
        _fail("FORMAT_CSV", name, "", str(exc), "Исправьте CSV")
    return normalize_rows(name, rows)


def read_folder(folder: str | Path) -> Experiment:
    folder = Path(folder)
    if not folder.is_dir():
        _fail("FORMAT_FOLDER", str(folder), "", "папка не найдена", "Выберите папку выгрузки")
    names = {p.name for p in folder.iterdir() if p.is_file()}
    if names != set(FILES):
        _fail("FORMAT_FILES", str(folder), "", f"ожидаются ровно четыре файла: {', '.join(FILES)}", "Уберите лишние файлы и добавьте недостающие")
    originals = {name: (folder / name).read_bytes() for name in FILES}
    passport = parse_passport(originals["experiment.json"])
    tables = {name: parse_csv(name, originals[name]) for name in HEADERS}
    experiment = Experiment(passport, tables, originals, "import")
    report = validate_experiment(experiment, originals=originals, complete=True)
    errors = [item for item in report if item.severity == "error"]
    if errors:
        raise DataError(errors[0])
    return experiment


def from_manual(passport: dict[str, Any], tables: dict[str, list[dict[str, Any]]]) -> Experiment:
    """Use exactly the same normalization and validation as file input after CSV parsing."""
    return Experiment(passport, {name: normalize_rows(name, tables.get(name, [])) for name in HEADERS})


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def export_four(experiment: Experiment, folder: str | Path) -> None:
    """Export a complete valid input, creating the destination only after validation."""
    report = validate_experiment(experiment, complete=False)
    errors = [item for item in report if item.severity == "error"]
    if errors:
        raise DataError(errors[0])
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        _fail("STORAGE_DEST", str(folder), "", "папка не пуста", "Выберите пустую папку")
    csv_bytes: dict[str, bytes] = {}
    for name, header in HEADERS.items():
        out = io.StringIO(newline="")
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow(header)
        for row in experiment.tables[name]:
            writer.writerow([_cell(row[key]) for key in header])
        csv_bytes[name] = out.getvalue().encode("utf-8")
    passport = json.loads(json.dumps(experiment.passport, ensure_ascii=False))
    passport["format_name"] = "PAK_ROUTING_EXPORT_4F"
    passport["schema_version"] = "1.0.0"
    if experiment.source != "import":
        passport["data_origin"] = "manual_prepared"
    passport.setdefault("file_contract", {})
    passport["file_contract"].update(file_contract())
    passport.setdefault("export_status", {})
    passport["export_status"].update({
        "complete": True,
        "tables": {name: {
            "row_count": len(experiment.tables[name]),
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        } for name, data in csv_bytes.items()},
    })
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in csv_bytes.items():
        (folder / name).write_bytes(data)
    encoded = (json.dumps(passport, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    temporary = folder / "experiment.json.tmp"
    temporary.write_bytes(encoded)
    temporary.replace(folder / "experiment.json")
