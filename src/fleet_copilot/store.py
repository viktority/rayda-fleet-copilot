"""SQLite persistence. Every application read is scoped to a trusted principal."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .domain import FleetError, Principal, Snapshot, utcnow


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    company TEXT NOT NULL, device TEXT NOT NULL, ts TEXT NOT NULL,
                    source_line INTEGER NOT NULL, source_sha TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(company,device,ts));
                CREATE INDEX IF NOT EXISTS snapshots_company_time ON snapshots(company,ts);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL, actor TEXT NOT NULL,
                    digest TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_by TEXT,
                    UNIQUE(company,actor,digest));
                CREATE TABLE IF NOT EXISTS effects (
                    proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),
                    company TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, company TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, company TEXT NOT NULL, actor TEXT NOT NULL,
                    event TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                    BEGIN SELECT RAISE(ABORT, 'Audit records are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                    BEGIN SELECT RAISE(ABORT, 'Audit records are append-only'); END;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, source: Path) -> dict:
        raw = source.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        prepared = []
        seen = set()
        for line_no, line in enumerate(raw.decode().splitlines(), 1):
            if not line.strip():
                continue
            try:
                snapshot = Snapshot.model_validate_json(line)
            except ValueError as exc:
                # Do not echo raw telemetry or validation input to a client/log.
                raise FleetError("invalid_dataset", f"Invalid snapshot on source line {line_no}") from exc
            row = snapshot.model_dump(mode="json", exclude_none=True)
            key = (row["company_id"], row["device_id"], row["collected_at"])
            if key in seen:
                raise FleetError("duplicate_snapshot", f"Duplicate snapshot on source line {line_no}")
            seen.add(key)
            prepared.append((*key, line_no, sha, canonical(row)))
        if not prepared:
            raise FleetError("invalid_dataset", "Dataset contains no snapshots")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT value FROM metadata WHERE key='source_sha'").fetchone()
            if current is None or current[0] != sha:
                db.execute("DELETE FROM snapshots")
                db.executemany("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", prepared)
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('source_sha',?)", (sha,))
        return {"snapshots": len(prepared), "source_sha256": sha}

    def rows(self, principal: Principal, device_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM snapshots WHERE company=?", [principal.company_id]
        if device_id:
            sql += " AND device=?"
            args.append(device_id)
        with self.connection() as db:
            results = db.execute(sql + " ORDER BY device,ts", args).fetchall()
        if not results:
            raise FleetError("not_found", "No accessible telemetry found", 404)
        return [
            {**json.loads(row["payload"]), "_line": row["source_line"], "_sha": row["source_sha"]}
            for row in results
        ]

    def source_sha(self, db=None) -> str:
        if db is not None:
            return db.execute("SELECT value FROM metadata WHERE key='source_sha'").fetchone()[0]
        with self.connection() as conn:
            return self.source_sha(conn)

    @staticmethod
    def audit_in(db, principal: Principal, event: str, detail: dict):
        db.execute(
            "INSERT INTO audit(company,actor,event,created_at,detail) VALUES (?,?,?,?,?)",
            (principal.company_id, principal.actor_id, event, utcnow(), canonical(detail)),
        )

    def audit(self, principal: Principal, event: str, detail: dict):
        with self.connection() as db:
            self.audit_in(db, principal, event, detail)

    def audit_log(self, principal: Principal) -> list[dict]:
        if principal.role != "admin":
            raise FleetError("forbidden", "Administrator role required", 403)
        with self.connection() as db:
            rows = db.execute("SELECT * FROM audit WHERE company=? ORDER BY id", (principal.company_id,))
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def save_run(self, principal: Principal, run: dict):
        with self.connection() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?)",
                (run["run_id"], principal.company_id, principal.actor_id, canonical(run)),
            )
            self.audit_in(db, principal, "run_completed", {"run_id": run["run_id"], "status": run["status"]})

    def get_run(self, principal: Principal, run_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM runs WHERE company=? AND id=?", (principal.company_id, run_id)
            ).fetchone()
        if row is None:
            raise FleetError("not_found", "Run not found", 404)
        return json.loads(row[0])
