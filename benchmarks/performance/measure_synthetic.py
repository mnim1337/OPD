"""Exploratory K0–K2 timing on the five supplied synthetic controls.

This measures one process run, not an accepted load limit or calculation error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shlex
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zipfile import ZipFile

from scanner.core import calculate
from scanner.io.format import read_folder


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def _dependency(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("outputs/runs"))
    parser.add_argument("--journal", type=Path, default=Path("benchmarks/statistics/runs.jsonl"))
    args = parser.parse_args()
    if not args.run_id or "/" in args.run_id or "\\" in args.run_id:
        parser.error("run-id must be a single path component")
    output = args.output_root / args.run_id
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    commit = _git("rev-parse", "HEAD")
    tree = _git("status", "--porcelain=v1")
    diff_artifact = None
    if tree:
        diff_artifact = str(output / "working-tree.diff")
        (output / "working-tree.diff").write_text(_git("diff", "HEAD") + "\n", encoding="utf-8")
    result = {
        "run_id": args.run_id,
        "started_utc": started,
        "finished_utc": None,
        "purpose": "Предварительное время К0–К2 на пяти синтетических контрольных опытах",
        "method": "Последовательный импорт и calculate каждого комплекта в одном процессе; perf_counter_ns и tracemalloc peak",
        "command": shlex.join(sys.argv),
        "parameters": {"case_count_expected": 5, "timing_unit": "s", "memory_unit": "byte"},
        "code_commit": commit,
        "tree_state": "clean" if not tree else tree,
        "diff_artifact": diff_artifact,
        "dataset": {"id": "PAK_5_experiments_with_validation", "version": "provided-2026-10-23",
                    "archive": str(args.archive), "sha256": None, "profile_sha256": None},
        "reference": None,
        "reference_reason": "Численная точность в этом запуске не оценивается; отдельный контроль находится в tests/numerical.",
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0],
                        "processor": platform.processor()},
        "dependencies": {"PySide6_Essentials": _dependency("PySide6_Essentials")},
        "seed": None,
        "seed_reason": "Случайные процедуры в измерении не применяются.",
        "accuracy": None,
        "accuracy_reason": "Измеряются время и Python-выделения памяти, ошибки показателей не сравниваются с эталоном.",
        "effectiveness": None,
        "effectiveness_reason": "Нагрузочные пределы Q-07 не согласованы; функциональный критерий — импорт и расчёт всех пяти случаев.",
        "artifacts": [str(output / "metrics.json")],
        "external_artifact_uri": None,
    }
    cases = []
    error = None
    try:
        result["dataset"]["sha256"] = _sha256(args.archive)
        result["dataset"]["profile_sha256"] = _sha256(args.profile)
        profile = json.loads(args.profile.read_text(encoding="utf-8"))
        with ZipFile(args.archive) as archive:
            folders = sorted({str(Path(name).parent) for name in archive.namelist()
                              if "/experiments/" in name and name.endswith("experiment.json")})
            if len(folders) != 5:
                raise ValueError(f"Ожидалось пять комплектов, получено {len(folders)}")
            with tempfile.TemporaryDirectory() as temporary:
                for index, folder_name in enumerate(folders):
                    folder = Path(temporary) / str(index)
                    folder.mkdir()
                    for name in archive.namelist():
                        if str(Path(name).parent) == folder_name and Path(name).name in {
                            "experiment.json", "packets.csv", "routes.csv", "channels.csv"
                        }:
                            (folder / Path(name).name).write_bytes(archive.read(name))
                    tracemalloc.start()
                    began = time.perf_counter_ns()
                    experiment = read_folder(folder)
                    imported_ns = time.perf_counter_ns()
                    assessment = calculate(experiment, profile)
                    completed_ns = time.perf_counter_ns()
                    _, peak_bytes = tracemalloc.get_traced_memory()
                    tracemalloc.stop()
                    cases.append({
                        "experiment_id": experiment.experiment_id,
                        "rows": {name: len(rows) for name, rows in experiment.tables.items()},
                        "import_s": (imported_ns - began) / 1_000_000_000,
                        "calculate_s": (completed_ns - imported_ns) / 1_000_000_000,
                        "peak_python_allocations_bytes": peak_bytes,
                        "integral_status": assessment.integral.status,
                    })
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        result["effectiveness"] = {
            "status": "completed" if error is None else "failed",
            "criterion": "Все пять синтетических комплектов импортированы и рассчитаны",
            "criterion_met": error is None and len(cases) == 5,
            "successful_cases": len(cases), "total_cases": 5,
            "performance_criterion_met": None,
        }
        if error is not None:
            result["error"] = error
        (output / "metrics.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with args.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    print(json.dumps({"run_id": args.run_id, "successful_cases": len(cases),
                      "artifacts": result["artifacts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
