"""
services/knowledge.py — What spooky has seen and heard, and what it has learnt from it.

Three layers, as in a person's memory:

  observations  everything perceived, as text: what was said around it (every final
                Vosk transcript — the audio itself is never kept), what it saw (scene
                descriptions, objects), who it met, what it said and was told.
                Short-lived: kept until consolidated, deleted `keep_observations_h` later.
  knowledge     lasting facts the language model distils from the observations
                ("Samuele drinks coffee at nine", "the printer jams on Mondays"), each
                with an embedding, a confidence, and how often it was confirmed.
  recall        before every reply, the facts and recent observations closest in
                meaning to what is being talked about go into the prompt.

Consolidation is spooky's sleep: at night (or when many observations are waiting, or
on request) the model reads the day in chunks and extracts facts. A fact that matches
one already known reinforces it instead of duplicating it; facts never confirmed fade
after `forget_after_days`.

No model is retrained: the language model stays as it is and the learning lives here,
so spooky gets to know people without getting worse, and forgetting a person removes
them for good.

Built for the Pi: SQLite, embeddings as float16 blobs, the knowledge matrix cached in
RAM (thousands of facts recalled in well under a millisecond). Embedding and
consolidation run on the Mac's Ollama, batched; while it is away, observations queue
and are embedded later, and recall falls back to the most recent ones.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

import numpy as np

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS observations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    kind         TEXT    NOT NULL,          -- heard | told | said | seen | met
    text         TEXT    NOT NULL,
    who          TEXT    NOT NULL DEFAULT '',
    emb          BLOB,
    consolidated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_obs_ts   ON observations(ts);
CREATE INDEX IF NOT EXISTS idx_obs_cons ON observations(consolidated);
CREATE TABLE IF NOT EXISTS knowledge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT    NOT NULL,
    about      TEXT    NOT NULL DEFAULT '',
    confidence REAL    NOT NULL DEFAULT 0.5,
    count      INTEGER NOT NULL DEFAULT 1,
    created    REAL    NOT NULL,
    updated    REAL    NOT NULL,
    emb        BLOB
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_LABEL = {"heard": "heard", "told": "was told", "said": "said", "seen": "saw", "met": "met"}


class OllamaEmbedder:
    """Unit-length embeddings from Ollama (embeddinggemma: multilingual, 768 numbers)."""

    def __init__(self, model: str = "embeddinggemma", timeout_s: float = 15.0) -> None:
        self.model = model
        self._timeout = timeout_s
        self._client = None

    def embed(self, texts: Sequence[str], query: bool = False) -> Optional[np.ndarray]:
        if not texts:
            return np.zeros((0, 0), np.float32)
        # embeddinggemma's own prompts for retrieval: queries and documents differ.
        prefix = "task: search result | query: " if query else "title: none | text: "
        try:
            if self._client is None:
                import ollama
                self._client = ollama.Client(timeout=self._timeout)
            resp = self._client.embed(model=self.model, input=[prefix + t for t in texts])
            e = np.asarray(resp.embeddings if hasattr(resp, "embeddings") else resp["embeddings"],
                           np.float32)
            return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)
        except Exception as exc:
            log.debug(f"Embedder: {exc}")
            return None


def _ollama_chat(model: str) -> Callable[[str], str]:
    def chat(prompt: str) -> str:
        from services import llm_host
        resp = llm_host.client().chat(model=model, messages=[{"role": "user", "content": prompt}],
                           format="json", options={"temperature": 0.2, "num_predict": 700},
                           keep_alive=600)
        return resp.message.content if hasattr(resp, "message") else resp["message"]["content"]
    return chat


def _blob(v: np.ndarray) -> bytes:
    return v.astype(np.float16).tobytes()


def _vec(b: Optional[bytes]) -> Optional[np.ndarray]:
    return None if b is None else np.frombuffer(b, np.float16).astype(np.float32)


class KnowledgeService:

    def __init__(self, bus: EventBus, db_path: Path, cfg=None,
                 embedder=None, chat: Optional[Callable[[str], str]] = None,
                 clock: Callable[[], float] = time.time,
                 moving_fn: Optional[Callable[[], bool]] = None) -> None:
        get = (lambda k, d: cfg.get(f"knowledge.{k}", d)) if cfg is not None else (lambda k, d: d)
        self._bus = bus
        self._clock = clock
        self._moving_fn = moving_fn           # the body walking: servo noise, not words
        self._embedder = embedder or OllamaEmbedder(get("embed_model", "embeddinggemma"))
        self._chat = chat or _ollama_chat(get("model", "gemma3:12b"))
        self._min_words = int(get("min_words", 3))
        self._keep_obs_s = float(get("keep_observations_h", 48)) * 3600
        self._recent_s = float(get("recent_days", 2)) * 86400
        self._forget_s = float(get("forget_after_days", 60)) * 86400
        self._max_pending = int(get("consolidate_when_pending", 300))
        self._hour = int(get("consolidate_hour", 3))
        self._same = float(get("same_fact_cosine", 0.85))
        self._min_sim = float(get("recall_min_cosine", 0.45))

        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._consolidating = threading.Lock()
        self._ambient = self._meta("ambient", str(bool(get("ambient", True)))) == "True"
        self._who = ("", 0.0)                       # the person in view, and when
        self._met: dict[str, float] = {}
        self._last_heard = ""
        self._matrix: Optional[np.ndarray] = None   # knowledge embeddings, cached
        self._matrix_ids: list[int] = []
        self._wake = threading.Event()
        self._active = False

        for event, handler in ((EventType.SPEECH_TRANSCRIBED, self._on_heard),
                               (EventType.COMMAND_PARSED, self._on_told),
                               (EventType.TTS_STARTED, self._on_said),
                               (EventType.SCENE_ANALYZED, self._on_seen),
                               (EventType.OBJECTS_DETECTED, self._on_objects),
                               (EventType.PERSON_IDENTIFIED, self._on_met),
                               (EventType.PERSON_UNKNOWN, self._on_stranger),
                               (EventType.EXPRESSION_CHANGED, self._on_expression)):
            bus.subscribe(event, handler)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        threading.Thread(target=self._worker, daemon=True, name="Knowledge").start()
        log.info(f"KnowledgeService: {self.stats()['facts']} facts known, ambient listening "
                 f"{'on' if self._ambient else 'off'}")

    def stop(self) -> None:
        self._active = False
        self._wake.set()

    # ── what comes in ─────────────────────────────────────────────────────────

    def observe(self, kind: str, text: str, who: str = "") -> None:
        text = re.sub(r"\s+", " ", text or "").strip()
        if not text:
            return
        with self._lock:
            self._db.execute("INSERT INTO observations(ts, kind, text, who) VALUES (?,?,?,?)",
                             (self._clock(), kind, text[:600], who or self._present()))
            self._db.commit()

    def _present(self) -> str:
        name, seen = self._who
        return name if self._clock() - seen < 15 else ""

    def _on_heard(self, ev) -> None:
        text = (ev.get("text") or "").strip()
        if not ev.get("final") or not self._ambient or len(text.split()) < self._min_words:
            return
        if ev.get("own") or (self._moving_fn is not None and self._moving_fn()):
            return                            # its own voice, or its own motors
        if text == self._last_heard:
            return
        self._last_heard = text
        self.observe("heard", text)

    def _on_told(self, ev) -> None:
        self.observe("told", ev.get("command", ""))

    def _on_said(self, ev) -> None:
        self.observe("said", ev.get("text", ""))

    def _on_seen(self, ev) -> None:
        self.observe("seen", ev.get("description") or ev.get("text", ""))

    def _on_objects(self, ev) -> None:
        objects = ev.get("objects") or ev.get("text", "")
        if objects:
            self.observe("seen", f"Objects in view: {objects}")

    def _on_met(self, ev) -> None:
        name = ev.get("display_name") or ev.get("person_id") or ""
        if not name:
            return
        now = self._clock()
        self._who = (name, now)
        if now - self._met.get(name, 0) > 600:          # one entry per visit, not per frame
            self._met[name] = now
            self.observe("met", f"{name} was here", who=name)

    def _on_expression(self, ev) -> None:
        expression = ev.get("expression")
        if expression and expression != "neutral":
            self.observe("seen", f"{ev.get('name') or 'Someone'} looked {expression}", who=ev.get("name") or "")

    def _on_stranger(self, ev) -> None:
        now = self._clock()
        if now - self._met.get("?", 0) > 600:
            self._met["?"] = now
            self.observe("met", "Someone I don't know was here")

    # ── recall ────────────────────────────────────────────────────────────────

    def recall(self, query: str, k: int = 6) -> List[str]:
        """What spooky remembers that bears on `query`, as short lines for a prompt."""
        q = self._embedder.embed([query], query=True) if query else None
        lines: list[tuple[float, str]] = []
        now = self._clock()
        if q is not None and len(q):
            q = q[0]
            ids, matrix = self._knowledge_matrix()
            if matrix is not None:
                sims = matrix @ q
                with self._lock:
                    for i in np.argsort(-sims)[:k]:
                        if sims[i] < self._min_sim:
                            break
                        row = self._db.execute("SELECT text, about FROM knowledge WHERE id=?",
                                               (ids[i],)).fetchone()
                        if row:
                            lines.append((float(sims[i]) + 0.05, f"[known] {row[0]}"))
            with self._lock:
                rows = self._db.execute(
                    "SELECT ts, kind, text, who, emb FROM observations "
                    "WHERE emb IS NOT NULL AND ts > ?", (now - self._recent_s,)).fetchall()
            for ts, kind, text, who, emb in rows:
                sim = float(_vec(emb) @ q)
                if sim >= self._min_sim:
                    fresh = 0.05 * np.exp(-(now - ts) / 86400)     # a little preference for recent
                    lines.append((sim + fresh, self._line(ts, kind, text, who, now)))
        # What happened in the last few minutes is always context, embedded or not.
        with self._lock:
            recent = self._db.execute(
                "SELECT ts, kind, text, who FROM observations WHERE ts > ? AND kind != 'said' "
                "ORDER BY ts DESC LIMIT 4", (now - 300,)).fetchall()
        for ts, kind, text, who in recent:
            lines.append((0.0, self._line(ts, kind, text, who, now)))
        seen, out = set(), []
        for _, line in sorted(lines, key=lambda sl: -sl[0]):
            if line not in seen:
                seen.add(line)
                out.append(line)
        return out[:k + 2]

    @staticmethod
    def _line(ts: float, kind: str, text: str, who: str, now: float) -> str:
        ago = now - ts
        when = ("just now" if ago < 120 else f"{int(ago // 60)} min ago" if ago < 3600
                else f"{int(ago // 3600)} h ago" if ago < 86400 else f"{int(ago // 86400)} days ago")
        return f"[{_LABEL.get(kind, kind)} {when}{', ' + who + ' there' if who else ''}] {text}"

    def _knowledge_matrix(self):
        with self._lock:
            if self._matrix is None:
                rows = self._db.execute("SELECT id, emb FROM knowledge WHERE emb IS NOT NULL").fetchall()
                self._matrix_ids = [r[0] for r in rows]
                self._matrix = np.stack([_vec(r[1]) for r in rows]) if rows else None
            return self._matrix_ids, self._matrix

    def _invalidate(self) -> None:
        with self._lock:
            self._matrix = None

    # ── consolidation: spooky's sleep ─────────────────────────────────────────

    def request_consolidation(self) -> None:
        self._meta("consolidate_now", "1", write=True)
        self._wake.set()

    def consolidate(self, chunk_lines: int = 80) -> int:
        """Distil unconsolidated observations into lasting facts. Returns facts learnt or reinforced."""
        if not self._consolidating.acquire(blocking=False):
            return 0
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT id, ts, kind, text, who FROM observations WHERE consolidated=0 "
                    "ORDER BY ts").fetchall()
            changed = 0
            for start in range(0, len(rows), chunk_lines):
                chunk = rows[start:start + chunk_lines]
                facts = self._extract(chunk)
                if facts is None:
                    break                                  # the Mac is away: try again later
                changed += self._merge(facts)
                with self._lock:
                    self._db.executemany("UPDATE observations SET consolidated=1 WHERE id=?",
                                         [(r[0],) for r in chunk])
                    self._db.commit()
            self._tidy()
            self._meta("last_consolidation", str(self._clock()), write=True)
            self._meta("consolidate_now", "0", write=True)
            log.info(f"KnowledgeService: consolidated {len(rows)} observations, {changed} facts learnt or reinforced")
            return changed
        finally:
            self._consolidating.release()

    def _extract(self, chunk) -> Optional[list]:
        lines = "\n".join(
            f"{time.strftime('%a %H:%M', time.localtime(ts))} [{_LABEL.get(kind, kind)}"
            f"{', ' + who + ' there' if who else ''}] {text}" for _, ts, kind, text, who in chunk)
        known = self.recall(lines[-1500:], k=15)
        prompt = (
            "You are the long-term memory of Spooky, a small robot spider on a desk. Below is "
            "what it perceived: what people said around it (Italian, transcribed by speech "
            "recognition, so expect errors), what it saw, who it met and what it said.\n\n"
            "Extract the facts worth remembering for weeks: about people (names, roles, habits, "
            "preferences, plans, relationships, what they told Spooky), about the place "
            "(objects, routines, schedules) and about Spooky itself. Skip small talk, "
            "recognition garbage, and anything uncertain. Never invent.\n\n"
            "Already known (repeat a fact only if it changed):\n"
            + ("\n".join(f"- {k}" for k in known if k.startswith("[known]")) or "- nothing yet")
            + f"\n\nPerceived:\n{lines}\n\n"
            'Answer with JSON: {"facts": [{"about": "<person name, or place, or spooky>", '
            '"fact": "<one short English sentence>", "confidence": <0..1>}]} — an empty list '
            "if nothing is worth keeping."
        )
        try:
            reply = json.loads(self._chat(prompt))
        except Exception as exc:
            log.warning(f"KnowledgeService: consolidation postponed ({exc})")
            return None
        facts = reply.get("facts", []) if isinstance(reply, dict) else reply
        return [f for f in facts if isinstance(f, dict) and str(f.get("fact", "")).strip()]

    def _merge(self, facts: list) -> int:
        texts = [str(f["fact"]).strip()[:300] for f in facts]
        embs = self._embedder.embed(texts)
        if embs is None:
            return 0
        now, changed = self._clock(), 0
        ids, matrix = self._knowledge_matrix()
        ids = list(ids)
        rows = list(matrix) if matrix is not None else []    # grows as this batch adds facts
        with self._lock:
            for fact, text, e in zip(facts, texts, embs):
                conf = float(min(1.0, max(0.0, fact.get("confidence", 0.6))))
                about = str(fact.get("about", ""))[:60]
                sims = np.stack(rows) @ e if rows else np.zeros(0)
                best = int(np.argmax(sims)) if len(sims) else -1
                if best >= 0 and float(sims[best]) >= self._same:
                    # Heard again: more certain, and the newer wording wins (facts change).
                    self._db.execute(
                        "UPDATE knowledge SET text=?, about=?, emb=?, count=count+1, updated=?, "
                        "confidence=MIN(1.0, MAX(confidence, ?) + 0.05) WHERE id=?",
                        (text, about, _blob(e), now, conf, ids[best]))
                    rows[best] = e
                else:
                    cur = self._db.execute(
                        "INSERT INTO knowledge(text, about, confidence, created, updated, emb) "
                        "VALUES (?,?,?,?,?,?)", (text, about, conf, now, now, _blob(e)))
                    ids.append(cur.lastrowid)
                    rows.append(e)
                changed += 1
            self._db.commit()
        self._invalidate()
        return changed

    def _tidy(self) -> None:
        now = self._clock()
        with self._lock:
            # Raw text goes once it has been learnt from; unconfirmed facts fade.
            self._db.execute("DELETE FROM observations WHERE consolidated=1 AND ts < ?",
                             (now - self._keep_obs_s,))
            self._db.execute("DELETE FROM knowledge WHERE count < 2 AND confidence < 0.6 AND updated < ?",
                             (now - self._forget_s,))
            self._db.commit()
        self._invalidate()

    # ── the worker: embeddings, scene dedup, the nightly sleep ────────────────

    def _worker(self) -> None:
        while self._active:
            self._wake.wait(20)
            self._wake.clear()
            if not self._active:
                break
            try:
                self.embed_pending()
                if self._due():
                    self.consolidate()
            except Exception as exc:
                log.warning(f"KnowledgeService worker: {exc}")

    def embed_pending(self, batch: int = 32) -> int:
        with self._lock:
            rows = self._db.execute("SELECT id, kind, text FROM observations WHERE emb IS NULL "
                                    "ORDER BY ts LIMIT ?", (batch,)).fetchall()
        if not rows:
            return 0
        embs = self._embedder.embed([r[2] for r in rows])
        if embs is None:
            return 0
        with self._lock:
            last_seen = self._db.execute("SELECT emb FROM observations WHERE kind='seen' AND emb IS NOT NULL "
                                         "ORDER BY ts DESC LIMIT 1").fetchone()
            prev = _vec(last_seen[0]) if last_seen else None
            for (oid, kind, _), e in zip(rows, embs):
                if kind == "seen" and prev is not None and float(prev @ e) > 0.93:
                    self._db.execute("DELETE FROM observations WHERE id=?", (oid,))   # same view again
                    continue
                if kind == "seen":
                    prev = e
                self._db.execute("UPDATE observations SET emb=? WHERE id=?", (_blob(e), oid))
            self._db.commit()
        return len(rows)

    def _due(self) -> bool:
        if self._meta("consolidate_now", "0") == "1":
            return True
        stats = self.stats()
        if stats["pending"] >= self._max_pending:
            return True
        last = float(self._meta("last_consolidation", "0"))
        now = time.localtime(self._clock())
        return (stats["pending"] > 0 and now.tm_hour == self._hour
                and self._clock() - last > 12 * 3600)

    # ── dashboard and privacy ─────────────────────────────────────────────────

    @property
    def ambient(self) -> bool:
        return self._ambient

    def set_ambient(self, on: bool) -> None:
        self._ambient = bool(on)
        self._meta("ambient", str(self._ambient), write=True)
        log.info(f"KnowledgeService: ambient listening {'on' if on else 'off'}")

    def facts(self, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, text, about, confidence, count, updated FROM knowledge "
                                    "ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("id", "text", "about", "confidence", "count", "updated"), r)) for r in rows]

    def forget_fact(self, fact_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM knowledge WHERE id=?", (fact_id,))
            self._db.commit()
        self._invalidate()

    def forget_person(self, name: str) -> None:
        """Everything about `name`: facts, and what was perceived while they were there."""
        if not name:
            return
        with self._lock:
            self._db.execute("DELETE FROM knowledge WHERE lower(about)=lower(?) OR text LIKE ?",
                             (name, f"%{name}%"))
            self._db.execute("DELETE FROM observations WHERE lower(who)=lower(?) OR text LIKE ?",
                             (name, f"%{name}%"))
            self._db.commit()
        self._invalidate()

    def stats(self) -> dict:
        with self._lock:
            facts = self._db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
            pending = self._db.execute("SELECT COUNT(*) FROM observations WHERE consolidated=0").fetchone()[0]
            today = self._db.execute("SELECT COUNT(*) FROM observations WHERE ts > ?",
                                     (self._clock() - 86400,)).fetchone()[0]
        last = float(self._meta("last_consolidation", "0"))
        return {"facts": facts, "pending": pending, "observations_24h": today,
                "last_consolidation": last or None, "ambient": self._ambient,
                "consolidating": self._consolidating.locked()}

    def _meta(self, key: str, default: str, write: bool = False) -> str:
        with self._lock:
            if write:
                self._db.execute("INSERT INTO meta(key, value) VALUES (?,?) "
                                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, default))
                self._db.commit()
                return default
            row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
