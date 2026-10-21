from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ArtifactRecord, CollectionJob, PromptJob, RunState


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class AuditStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                """
            )

    def create_run(self, run_id: str, job: CollectionJob | PromptJob) -> None:
        now = utc_now()
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    RunState.RECEIVED,
                    job.objective,
                    job.requested_by,
                    json.dumps(asdict(job), sort_keys=True),
                    now,
                    now,
                ),
            )

    def set_state(self, run_id: str, state: RunState) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE runs SET state = ?, updated_at = ? WHERE run_id = ?",
                (state, utc_now(), run_id),
            )
        self.event(run_id, "state_changed", {"state": state})

    def event(self, run_id: str, event_type: str, detail: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(run_id, occurred_at, event_type, detail_json) VALUES (?, ?, ?, ?)",
                (run_id, utc_now(), event_type, json.dumps(detail, sort_keys=True)),
            )

    def artifact(self, run_id: str, record: ArtifactRecord) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?)",
                (record.artifact_id, run_id, json.dumps(record.to_dict(), sort_keys=True)),
            )

    def run_report(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            run = db.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if run is None:
                return None
            events = db.execute(
                "SELECT occurred_at, event_type, detail_json FROM events WHERE run_id = ? ORDER BY event_id",
                (run_id,),
            ).fetchall()
            artifacts = db.execute(
                "SELECT record_json FROM artifacts WHERE run_id = ? ORDER BY artifact_id", (run_id,)
            ).fetchall()
        return {
            "run": dict(run),
            "events": [
                {"occurred_at": row[0], "event_type": row[1], "detail": json.loads(row[2])}
                for row in events
            ],
            "artifacts": [json.loads(row[0]) for row in artifacts],
        }
