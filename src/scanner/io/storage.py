"""Transactional, append-only local draft revisions."""

from __future__ import annotations

import json
import hashlib
import sqlite3
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
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def save(self, draft_id: str, experiment: Experiment, profile: dict[str, Any] | None = None) -> int:
        return self.save_unvalidated(draft_id, experiment.passport, experiment.table_strings(), profile,
                                     source=experiment.source, originals=experiment.original_files)

    def save_unvalidated(self, draft_id: str, passport_data: dict[str, Any],
                         table_cells: dict[str, list[dict[str, str]]], profile: dict[str, Any] | None = None,
                         *, source: str = "manual", originals: dict[str, bytes] | None = None) -> int:
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

    def profile_version(self, draft_id: str, revision: int) -> tuple[str, int] | None:
        with self._connect() as db:
            return db.execute("SELECT profile_id, profile_version FROM draft_profiles WHERE draft_id = ? AND revision = ?", (draft_id, revision)).fetchone()
