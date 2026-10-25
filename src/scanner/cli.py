"""Small headless entry point for import and export checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import calculate
from .core.runner import CalculationError
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
    evaluation = sub.add_parser("calculate", help="рассчитать К0–К2 без GUI")
    evaluation.add_argument("folder", type=Path)
    evaluation.add_argument("--profile", type=Path, required=True)
    evaluation.add_argument("--output", type=Path)
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
        elif args.command == "export-draft":
            experiment, _, revision = DraftStore(args.store).load(args.id, args.revision)
            export_four(experiment, args.folder)
            print(f"{args.id}: версия {revision} экспортирована в {args.folder}")
        else:
            experiment = read_folder(args.folder)
            with args.profile.open(encoding="utf-8") as stream:
                profile = json.load(stream)
            report = calculate(experiment, profile)
            payload = json.dumps(report.as_dict(), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(payload, encoding="utf-8")
                print(f"Расчёт {experiment.experiment_id} записан в {args.output}")
            else:
                print(payload, end="")
    except (DataError, CalculationError, KeyError, OSError, ValueError) as exc:
        parser.exit(2, f"Ошибка: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
