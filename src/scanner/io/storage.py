"""Transactional, append-only local draft revisions."""

from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .format import from_manual
from .model import Experiment


class DraftStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_utc TEXT NOT NULL,
                    source TEXT NOT NULL,
                    passport_json TEXT NOT NULL,
                    tables_json TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    PRIMARY KEY (draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS originals (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    filename TEXT NOT NULL,
                    content BLOB NOT NULL,
                    PRIMARY KEY (draft_id, revision, filename),
                    FOREIGN KEY (draft_id, revision) REFERENCES drafts(draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS draft_copies (
                    draft_id TEXT PRIMARY KEY,
                    source_draft_id TEXT NOT NULL,
                    source_revision INTEGER NOT NULL,
                    FOREIGN KEY (source_draft_id, source_revision) REFERENCES drafts(draft_id, revision)
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    profile_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    PRIMARY KEY (profile_id, version)
                );
                CREATE TABLE IF NOT EXISTS draft_profiles (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    profile_id TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    PRIMARY KEY (draft_id, revision),
                    FOREIGN KEY (draft_id, revision) REFERENCES drafts(draft_id, revision),
                    FOREIGN KEY (profile_id, profile_version) REFERENCES profiles(profile_id, version)
                );
                CREATE TABLE IF NOT EXISTS assessments (
                    assessment_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    profile_id TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    model_version TEXT NOT NULL,
                    created_utc TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (draft_id, draft_revision) REFERENCES drafts(draft_id, revision),
                    FOREIGN KEY (profile_id, profile_version) REFERENCES profiles(profile_id, version)
                );
                CREATE TABLE IF NOT EXISTS series (
                    series_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
                    created_utc TEXT NOT NULL,
                    closed_utc TEXT
                );
                CREATE TABLE IF NOT EXISTS series_assessments (
                    series_id TEXT NOT NULL REFERENCES series(series_id),
                    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
                    position INTEGER NOT NULL,
                    added_utc TEXT NOT NULL,
                    PRIMARY KEY (series_id, assessment_id),
                    UNIQUE (series_id, position)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def save(self, draft_id: str, experiment: Experiment, profile: dict[str, Any] | None = None,
             *, copy_of: tuple[str, int] | None = None) -> int:
        return self.save_unvalidated(draft_id, experiment.passport, experiment.table_strings(), profile,
                                     source=experiment.source, originals=experiment.original_files,
                                     copy_of=copy_of)

    def save_unvalidated(self, draft_id: str, passport_data: dict[str, Any],
                         table_cells: dict[str, list[dict[str, str]]], profile: dict[str, Any] | None = None,
                         *, source: str = "manual", originals: dict[str, bytes] | None = None,
                         copy_of: tuple[str, int] | None = None) -> int:
        """Preserve even incomplete or mistyped cells so a draft can be repaired later."""
        if not draft_id or "/" in draft_id or "\\" in draft_id:
            raise ValueError("Нужен непустой идентификатор черновика без разделителей пути")
        passport = json.dumps(passport_data, ensure_ascii=False, allow_nan=False)
        tables = json.dumps(table_cells, ensure_ascii=False)
        profile_text = json.dumps(profile or {}, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT COALESCE(MAX(revision), 0) + 1 FROM drafts WHERE draft_id = ?", (draft_id,)).fetchone()[0]
            db.execute("INSERT INTO drafts VALUES (?, ?, ?, ?, ?, ?, ?)", (
                draft_id, revision, datetime.now(timezone.utc).isoformat(), source,
                passport, tables, profile_text,
            ))
            if copy_of is not None:
                if revision != 1:
                    raise ValueError("Связь копии задаётся только для нового черновика")
                db.execute("INSERT INTO draft_copies VALUES (?, ?, ?)",
                           (draft_id, copy_of[0], copy_of[1]))
            for name, data in (originals or {}).items():
                db.execute("INSERT INTO originals VALUES (?, ?, ?, ?)", (draft_id, revision, name, data))
            if profile and profile.get("profile_id"):
                profile_id = str(profile["profile_id"])
                digest = hashlib.sha256(profile_text.encode("utf-8")).hexdigest()
                previous = db.execute("SELECT version, sha256 FROM profiles WHERE profile_id = ? ORDER BY version DESC LIMIT 1", (profile_id,)).fetchone()
                if previous and previous[1] == digest:
                    profile_version = previous[0]
                else:
                    profile_version = (previous[0] if previous else 0) + 1
                    db.execute("INSERT INTO profiles VALUES (?, ?, ?, ?)", (profile_id, profile_version, digest, profile_text))
                db.execute("INSERT INTO draft_profiles VALUES (?, ?, ?, ?)", (draft_id, revision, profile_id, profile_version))
        return revision

    def load(self, draft_id: str, revision: int | None = None) -> tuple[Experiment, dict[str, Any], int]:
        passport, tables, source, originals, profile, rev = self.load_raw(draft_id, revision)
        experiment = from_manual(passport, tables)
        experiment.source = source
        experiment.original_files = originals
        return experiment, profile, rev

    def load_raw(self, draft_id: str, revision: int | None = None) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]], str, dict[str, bytes], dict[str, Any], int]:
        with self._connect() as db:
            if revision is None:
                row = db.execute("SELECT revision, source, passport_json, tables_json, profile_json FROM drafts WHERE draft_id = ? ORDER BY revision DESC LIMIT 1", (draft_id,)).fetchone()
            else:
                row = db.execute("SELECT revision, source, passport_json, tables_json, profile_json FROM drafts WHERE draft_id = ? AND revision = ?", (draft_id, revision)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            rev, source, passport_json, tables_json, profile_json = row
            originals = dict(db.execute("SELECT filename, content FROM originals WHERE draft_id = ? AND revision = ?", (draft_id, rev)))
        return json.loads(passport_json), json.loads(tables_json), source, originals, json.loads(profile_json), rev

    def list_drafts(self) -> list[tuple[str, int, str]]:
        with self._connect() as db:
            return db.execute("""
                SELECT d.draft_id, d.revision, d.created_utc FROM drafts d
                JOIN (SELECT draft_id, MAX(revision) AS revision FROM drafts GROUP BY draft_id) latest
                ON d.draft_id = latest.draft_id AND d.revision = latest.revision
                ORDER BY d.created_utc DESC
            """).fetchall()

    def matching_import(self, experiment: Experiment) -> tuple[str, int] | None:
        """Find a stored version with the same experiment ID and all four original files."""
        originals = experiment.original_files
        if set(originals) != {"experiment.json", "packets.csv", "routes.csv", "channels.csv"}:
            return None
        with self._connect() as db:
            rows = db.execute("""
                SELECT d.draft_id, d.revision
                FROM drafts d LEFT JOIN draft_copies c ON c.draft_id = d.draft_id
                WHERE c.draft_id IS NULL AND d.source IN ('import', 'edited_import')
                ORDER BY d.created_utc DESC, d.revision DESC
            """).fetchall()
            for draft_id, revision in rows:
                stored = dict(db.execute(
                    "SELECT filename, content FROM originals WHERE draft_id = ? AND revision = ?",
                    (draft_id, revision),
                ))
                if stored == originals and json.loads(stored["experiment.json"]).get(
                    "experiment_id"
                ) == experiment.experiment_id:
                    return draft_id, revision
        return None

    def copy_origin(self, draft_id: str) -> tuple[str, int] | None:
        with self._connect() as db:
            return db.execute(
                "SELECT source_draft_id, source_revision FROM draft_copies WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()

    def profile_version(self, draft_id: str, revision: int) -> tuple[str, int] | None:
        with self._connect() as db:
            return db.execute("SELECT profile_id, profile_version FROM draft_profiles WHERE draft_id = ? AND revision = ?", (draft_id, revision)).fetchone()

    def save_assessment(self, draft_id: str, revision: int, profile_id: str,
                        profile_version: int, model_version: str,
                        payload: dict[str, Any], assessment_id: str | None = None) -> str:
        """Append an immutable result referring to exact input/profile revisions."""
        assessment_id = assessment_id or str(uuid.uuid4())
        payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            linked_profile = db.execute(
                "SELECT profile_id, profile_version FROM draft_profiles WHERE draft_id = ? AND revision = ?",
                (draft_id, revision),
            ).fetchone()
            if linked_profile != (profile_id, profile_version):
                raise ValueError("Версия Ω не связана с зафиксированной версией входа")
            db.execute(
                "INSERT INTO assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (assessment_id, draft_id, revision, profile_id, profile_version,
                 model_version, datetime.now(timezone.utc).isoformat(), payload_json),
            )
        return assessment_id

    def list_assessments(self) -> list[tuple[str, str, int, str, str]]:
        with self._connect() as db:
            return db.execute(
                "SELECT assessment_id, draft_id, draft_revision, model_version, created_utc "
                "FROM assessments ORDER BY created_utc DESC"
            ).fetchall()

    def load_assessment(self, assessment_id: str) -> tuple[dict[str, Any], Experiment, dict[str, Any]]:
        with self._connect() as db:
            row = db.execute(
                "SELECT assessment_id, draft_id, draft_revision, profile_id, profile_version, payload_json "
                "FROM assessments WHERE assessment_id = ?", (assessment_id,),
            ).fetchone()
            if row is None:
                raise KeyError(assessment_id)
            stored_id, draft_id, revision, profile_id, profile_version, payload_json = row
            linked = db.execute(
                "SELECT content_json FROM profiles WHERE profile_id = ? AND version = ?",
                (profile_id, profile_version),
            ).fetchone()
        experiment, _, _ = self.load(draft_id, revision)
        if linked is None:
            raise ValueError("Сохранённая версия профиля Ω отсутствует")
        payload = json.loads(payload_json)
        payload["assessment_id"] = stored_id
        return payload, experiment, json.loads(linked[0])

    def backup(self, destination: str | Path) -> None:
        """Create a consistent SQLite backup while the application is open."""
        target = Path(destination)
        if target.resolve() == self.path.resolve():
            raise ValueError("Файл резервной копии должен отличаться от рабочего хранилища")
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(target) as backup:
            source.backup(backup)

    def restore(self, source_path: str | Path) -> None:
        """Validate a backup, copy it beside the live DB, then atomically replace it."""
        source_path = Path(source_path)
        if source_path.resolve() == self.path.resolve():
            raise ValueError("Выберите резервную копию, отличную от рабочего хранилища")
        temporary_path: Path | None = None
        try:
            with sqlite3.connect(source_path) as source:
                integrity = source.execute("PRAGMA integrity_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise ValueError("Резервная копия не прошла проверку целостности")
                tables = {row[0] for row in source.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )}
                required = {"drafts", "originals", "profiles", "draft_profiles", "assessments"}
                if not required.issubset(tables):
                    raise ValueError("В резервной копии отсутствуют таблицы OPD")
                foreign_key_errors = source.execute("PRAGMA foreign_key_check").fetchone()
                if foreign_key_errors is not None:
                    raise ValueError("В резервной копии нарушены связи между записями")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, name = tempfile.mkstemp(prefix=self.path.name + ".restore-",
                                                    suffix=".sqlite3", dir=self.path.parent)
                os.close(descriptor)
                temporary_path = Path(name)
                with sqlite3.connect(temporary_path) as destination:
                    source.backup(destination)
            os.replace(temporary_path, self.path)
            # Older valid OPD backups may not yet contain series tables.
            with self._connect() as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS draft_copies (
                        draft_id TEXT PRIMARY KEY,
                        source_draft_id TEXT NOT NULL,
                        source_revision INTEGER NOT NULL,
                        FOREIGN KEY (source_draft_id, source_revision) REFERENCES drafts(draft_id, revision)
                    );
                    CREATE TABLE IF NOT EXISTS series (
                        series_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
                        created_utc TEXT NOT NULL,
                        closed_utc TEXT
                    );
                    CREATE TABLE IF NOT EXISTS series_assessments (
                        series_id TEXT NOT NULL REFERENCES series(series_id),
                        assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id),
                        position INTEGER NOT NULL,
                        added_utc TEXT NOT NULL,
                        PRIMARY KEY (series_id, assessment_id),
                        UNIQUE (series_id, position)
                    );
                """)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def create_series(self, title: str) -> str:
        if not title.strip():
            raise ValueError("Укажите название серии")
        series_id = str(uuid.uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO series VALUES (?, ?, 'open', ?, NULL)",
                       (series_id, title.strip(), datetime.now(timezone.utc).isoformat()))
        return series_id

    def list_series(self) -> list[tuple[str, str, str, int]]:
        with self._connect() as db:
            return db.execute("""
                SELECT s.series_id, s.title, s.status, COUNT(sa.assessment_id)
                FROM series s LEFT JOIN series_assessments sa ON sa.series_id = s.series_id
                GROUP BY s.series_id ORDER BY s.created_utc DESC
            """).fetchall()

    def add_to_series(self, series_id: str, assessment_id: str) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM series WHERE series_id = ?", (series_id,)).fetchone()
            if row is None:
                raise KeyError(series_id)
            if row[0] != "open":
                raise ValueError("Нельзя добавлять оценки в завершённую серию")
            existing = db.execute(
                "SELECT position FROM series_assessments WHERE series_id = ? AND assessment_id = ?",
                (series_id, assessment_id),
            ).fetchone()
            if existing:
                return existing[0]
            position = db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM series_assessments WHERE series_id = ?",
                (series_id,),
            ).fetchone()[0]
            db.execute("INSERT INTO series_assessments VALUES (?, ?, ?, ?)",
                       (series_id, assessment_id, position, datetime.now(timezone.utc).isoformat()))
        return position

    def series_assessments(self, series_id: str) -> list[tuple[str, str, int, str, str]]:
        with self._connect() as db:
            return db.execute("""
                SELECT a.assessment_id, a.draft_id, a.draft_revision, a.model_version, a.created_utc
                FROM series_assessments sa JOIN assessments a USING (assessment_id)
                WHERE sa.series_id = ? ORDER BY sa.position
            """, (series_id,)).fetchall()

    def close_series(self, series_id: str) -> None:
        with self._connect() as db:
            result = db.execute(
                "UPDATE series SET status = 'closed', closed_utc = ? WHERE series_id = ? AND status = 'open'",
                (datetime.now(timezone.utc).isoformat(), series_id),
            )
            if result.rowcount == 0:
                raise KeyError(series_id)
