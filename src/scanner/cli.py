"""Small headless entry point for import and export checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .io.format import DataError, export_four, read_folder
from .io.storage import DraftStore
from .io.validation import validate_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description="OPD: проверка и подготовка четырёхфайловых комплектов")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="проверить папку из четырёх файлов")
    check.add_argument("folder", type=Path)
    save = sub.add_parser("save-draft", help="сохранить импорт как новую версию черновика")
    save.add_argument("folder", type=Path)
    save.add_argument("--store", type=Path, required=True)
    save.add_argument("--id", required=True)
    restore = sub.add_parser("export-draft", help="экспортировать версию черновика в четыре файла")
    restore.add_argument("--store", type=Path, required=True)
    restore.add_argument("--id", required=True)
    restore.add_argument("--revision", type=int)
    restore.add_argument("folder", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "check":
            experiment = read_folder(args.folder)
            warnings = [str(item) for item in validate_experiment(experiment, originals=experiment.original_files) if item.severity == "warning"]
            print(json.dumps({"experiment_id": experiment.experiment_id,
                              "rows": {name: len(rows) for name, rows in experiment.tables.items()},
                              "warnings": warnings}, ensure_ascii=False, indent=2))
        elif args.command == "save-draft":
            experiment = read_folder(args.folder)
            revision = DraftStore(args.store).save(args.id, experiment)
            print(f"{args.id}: версия {revision} сохранена")
        else:
            experiment, _, revision = DraftStore(args.store).load(args.id, args.revision)
            export_four(experiment, args.folder)
            print(f"{args.id}: версия {revision} экспортирована в {args.folder}")
    except (DataError, KeyError, OSError, ValueError) as exc:
        parser.exit(2, f"Ошибка: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
