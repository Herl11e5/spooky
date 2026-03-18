"""
services/memory.py — Persistent memory via SQLite.

Four memory classes (see architecture.md):
  1. Episodic   — timestamped interaction events
  2. Semantic   — stable facts (key/value + confidence)
  3. Social     — per-person profiles
  4. Procedural — skill parameter tuning

Design rules:
  - All writes are auto-committed (WAL mode for concurrent reads)
  - Reads never block the caller for more than a few ms
  - No foreign keys to keep schema simple
  - Confidence is 0.0 – 1.0; facts are only "promoted" after repetition
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS episodic (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    who         TEXT    NOT NULL DEFAULT 'unknown',
    what        TEXT    NOT NULL,
    action      TEXT    NOT NULL DEFAULT '',
    outcome     TEXT    NOT NULL DEFAULT '',
    confidence  REAL    NOT NULL DEFAULT 1.0,
    mode        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_episodic_ts  ON episodic(ts DESC);
CREATE INDEX IF NOT EXISTS idx_episodic_who ON episodic(who);

CREATE TABLE IF NOT EXISTS semantic_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 0.5,
    count       INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL,
    last_seen   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_key ON semantic_facts(key);

CREATE TABLE IF NOT EXISTS social_profiles (
    person_id               TEXT PRIMARY KEY,
    display_name            TEXT NOT NULL,
    familiarity             REAL NOT NULL DEFAULT 0.0,
    greeting_style          TEXT NOT NULL DEFAULT 'casual',
    interruption_tolerance  REAL NOT NULL DEFAULT 0.5,
    usual_hours_start       INTEGER NOT NULL DEFAULT 8,
    usual_hours_end         INTEGER NOT NULL DEFAULT 22,
    interaction_count       INTEGER NOT NULL DEFAULT 0,
    last_seen               REAL,
    notes                   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS procedural (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill       TEXT    NOT NULL,
    param_key   TEXT    NOT NULL,
    param_value TEXT    NOT NULL,
    confidence  REAL    NOT NULL DEFAULT 0.5,
    updated_at  REAL    NOT NULL,
    UNIQUE(skill, param_key)
);
CREATE INDEX IF NOT EXISTS idx_proc_skill ON procedural(skill);
"""

# Minimum repetition count before a semantic fact is "promoted"
_PROMOTE_THRESHOLD = 3


# ── MemoryService ─────────────────────────────────────────────────────────────

class MemoryService:
    """
    Thread-safe memory backend using SQLite.

    Every write goes through a dedicated writer thread to serialize
    disk I/O without blocking callers. Reads are synchronous but fast.
    """

    def __init__(self, bus: EventBus, db_path: Path = Path("data/memory.db")):
        self._bus     = bus
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local   = threading.local()   # per-thread connections for reads
        self._write_q: "queue.Queue" = __import__("queue").Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._active  = False
        # Initialise schema immediately so reads are safe before start() is called
        conn = sqlite3.connect(str(self._db_path))
        conn.executescript(_SCHEMA)
        conn.commit()
        conn.close()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._writer_thread = threading.Thread(
            target=self._write_loop, daemon=True, name="MemoryWriter"
        )
        self._writer_thread.start()
        log.info(f"MemoryService started — db={self._db_path}")

    def stop(self) -> None:
        self._active = False
        self._write_q.put(None)  # sentinel
        if self._writer_thread:
            self._writer_thread.join(timeout=5.0)
        log.info("MemoryService stopped")

    # ── connection helpers ────────────────────────────────────────────────────

    def _read_conn(self) -> sqlite3.Connection:
        """Per-thread read connection (SQLite is safe for concurrent reads in WAL)."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _enqueue_write(self, sql: str, params: tuple = ()) -> None:
        self._write_q.put((sql, params))

    def _write_loop(self) -> None:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        while self._active:
            item = self._write_q.get()
            if item is None:
                break
            sql, params = item
            try:
                conn.execute(sql, params)
                conn.commit()
            except Exception as e:
                log.error(f"MemoryService write error: {e} | sql={sql[:60]}")
        conn.close()

    # ── Episodic ──────────────────────────────────────────────────────────────

    def add_episode(
        self,
        what: str,
        who: str = "unknown",
        action: str = "",
        outcome: str = "",
        confidence: float = 1.0,
        mode: str = "",
    ) -> None:
        self._enqueue_write(
            "INSERT INTO episodic(ts, who, what, action, outcome, confidence, mode) "
            "VALUES (?,?,?,?,?,?,?)",
            (time.time(), who, what, action, outcome, confidence, mode),
        )

    def recent_episodes(
        self,
        n: int = 10,
        who: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._read_conn()
        if who:
            rows = conn.execute(
                "SELECT * FROM episodic WHERE who=? ORDER BY ts DESC LIMIT ?",
                (who, n),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM episodic ORDER BY ts DESC LIMIT ?", (n,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Semantic facts ─────────────────────────────────────────────────────────

    def upsert_fact(self, key: str, value: str, confidence: float = 0.5) -> None:
        now = time.time()
        # Try insert first; if key exists, increment count and update
        self._enqueue_write(
            """INSERT INTO semantic_facts(key, value, confidence, count, created_at, last_seen)
               VALUES (?,?,?,1,?,?)
               ON CONFLICT(key) DO UPDATE SET
                   value      = excluded.value,
                   confidence = MAX(semantic_facts.confidence, excluded.confidence),
                   count      = semantic_facts.count + 1,
                   last_seen  = excluded.last_seen""",
            (key, value, confidence, now, now),
        )
        # Check if it crosses the promotion threshold (async check after write)
        threading.Thread(target=self._maybe_promote, args=(key,), daemon=True).start()

    def get_fact(self, key: str) -> Optional[str]:
        row = self._read_conn().execute(
            "SELECT value FROM semantic_facts WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def all_facts(self) -> List[Dict[str, Any]]:
        rows = self._read_conn().execute(
            "SELECT * FROM semantic_facts ORDER BY confidence DESC, count DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def _maybe_promote(self, key: str) -> None:
        row = self._read_conn().execute(
            "SELECT count FROM semantic_facts WHERE key=?", (key,)
        ).fetchone()
        if row and row["count"] >= _PROMOTE_THRESHOLD:
            self._bus.publish(
                EventType.MEMORY_PROMOTED, {"key": key}, source="MemoryService"
            )

    # ── Social profiles ───────────────────────────────────────────────────────

    def upsert_person(
        self,
        person_id: str,
        display_name: str,
        familiarity_delta: float = 0.0,
    ) -> None:
        now = time.time()
        self._enqueue_write(
            """INSERT INTO social_profiles
                   (person_id, display_name, familiarity, last_seen, interaction_count)
               VALUES (?,?,?,?,1)
               ON CONFLICT(person_id) DO UPDATE SET
                   display_name      = excluded.display_name,
                   familiarity       = MIN(1.0, social_profiles.familiarity + ?),
                   last_seen         = excluded.last_seen,
                   interaction_count = social_profiles.interaction_count + 1""",
            (person_id, display_name, max(0.0, familiarity_delta), now, familiarity_delta),
        )

    def get_person(self, person_id: str) -> Optional[Dict[str, Any]]:
        row = self._read_conn().execute(
            "SELECT * FROM social_profiles WHERE person_id=?", (person_id,)
        ).fetchone()
        return dict(row) if row else None

    def all_persons(self) -> List[Dict[str, Any]]:
        rows = self._read_conn().execute(
            "SELECT * FROM social_profiles ORDER BY last_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_person_field(self, person_id: str, field: str, value: Any) -> None:
        allowed = {
            "greeting_style", "interruption_tolerance",
            "usual_hours_start", "usual_hours_end", "notes",
        }
        if field not in allowed:
            log.warning(f"update_person_field: disallowed field '{field}'")
            return
        self._enqueue_write(
            f"UPDATE social_profiles SET {field}=? WHERE person_id=?",
            (value, person_id),
        )

    def delete_person(self, person_id: str) -> None:
        self._enqueue_write(
            "DELETE FROM social_profiles WHERE person_id=?", (person_id,)
        )
        self._bus.publish(
            EventType.MEMORY_FORGOTTEN,
            {"person_id": person_id},
            source="MemoryService",
        )
        log.info(f"MemoryService: deleted person '{person_id}'")

    # ── Procedural ────────────────────────────────────────────────────────────

    def set_param(self, skill: str, key: str, value: Any, confidence: float = 0.7) -> None:
        self._enqueue_write(
            """INSERT INTO procedural(skill, param_key, param_value, confidence, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(skill, param_key) DO UPDATE SET
                   param_value = excluded.param_value,
                   confidence  = excluded.confidence,
                   updated_at  = excluded.updated_at""",
            (skill, key, json.dumps(value), confidence, time.time()),
        )

    def get_param(self, skill: str, key: str, default: Any = None) -> Any:
        row = self._read_conn().execute(
            "SELECT param_value FROM procedural WHERE skill=? AND param_key=?",
            (skill, key),
        ).fetchone()
        if row:
            try:
                return json.loads(row["param_value"])
            except Exception:
                return row["param_value"]
        return default

    def skill_params(self, skill: str) -> Dict[str, Any]:
        rows = self._read_conn().execute(
            "SELECT param_key, param_value FROM procedural WHERE skill=?", (skill,)
        ).fetchall()
        return {r["param_key"]: json.loads(r["param_value"]) for r in rows}

    # ── Utilities ─────────────────────────────────────────────────────────────

    def summary(self, max_episodes: int = 5) -> str:
        """Return a short human-readable summary for LLM context injection."""
        episodes = self.recent_episodes(max_episodes)
        persons  = self.all_persons()
        facts    = self.all_facts()

        lines = []
        if persons:
            names = ", ".join(p["display_name"] for p in persons[:5])
            lines.append(f"Persone note: {names}")
        if facts:
            top = facts[:5]
            lines.append("Fatti: " + "; ".join(f"{f['key']}={f['value']}" for f in top))
        if episodes:
            for ep in episodes:
                lines.append(f"- [{ep['who']}] {ep['what']}")
        return "\n".join(lines) if lines else "(nessun ricordo)"

    def __repr__(self) -> str:
        try:
            conn = self._read_conn()
            ep = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
            sf = conn.execute("SELECT COUNT(*) FROM semantic_facts").fetchone()[0]
            sp = conn.execute("SELECT COUNT(*) FROM social_profiles").fetchone()[0]
            return f"<MemoryService episodic={ep} facts={sf} persons={sp}>"
        except Exception:
            return "<MemoryService (db not ready)>"
