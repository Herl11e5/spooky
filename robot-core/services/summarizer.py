"""
services/summarizer.py — Daily episodic memory → semantic fact compression.

Runs:
  1. On a daily schedule (configurable hour, default 03:00)
  2. When episodic count exceeds a threshold (default 200)
  3. On explicit request via summarize_now()

Process:
  - Load recent episodes (last 24h by default)
  - Group by person and topic
  - Generate compact summaries
    - Template path: fast, no LLM, always available
    - LLM path: richer summaries when ollama is available + lock is free
  - Store results as semantic facts with high confidence
  - Optionally delete compressed episodes (default: keep, just mark)
  - Log outcome to episodic memory

Published events: none (writes directly to memory)
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from core.bus import EventBus, EventType
from services.memory import MemoryService

log = logging.getLogger(__name__)

# Shared ollama lock (injected at startup via set_ollama_lock)
_OLLAMA_LOCK: Optional[threading.Lock] = None

def set_ollama_lock(lock: threading.Lock) -> None:
    global _OLLAMA_LOCK
    _OLLAMA_LOCK = lock


class Summarizer:
    """
    Compresses episodic memory into semantic facts on a daily schedule.
    """

    DEFAULT_HOUR        = 3     # 03:00
    EPISODE_THRESHOLD   = 200   # trigger early if this many episodes
    LOOKBACK_HOURS      = 24

    def __init__(
        self,
        bus: EventBus,
        memory: MemoryService,
        cfg,
        llm_model: Optional[str] = None,
    ):
        self._bus       = bus
        self._memory    = memory
        self._cfg       = cfg
        self._llm_model = llm_model

        self._hour    = int(cfg.get("summarizer.hour", self.DEFAULT_HOUR))
        self._active  = False
        self._thread: Optional[threading.Thread] = None
        self._last_summary_date: Optional[str] = None   # "YYYY-MM-DD"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._schedule_loop, daemon=True, name="Summarizer"
        )
        self._thread.start()
        log.info(f"Summarizer scheduled at {self._hour:02d}:00 daily")

    def stop(self) -> None:
        self._active = False

    def summarize_now(self, lookback_h: int = 24) -> str:
        """
        Trigger summarization immediately. Returns summary text.
        Blocks until complete.
        """
        log.info("Summarizer: running on demand")
        return self._run(lookback_h)

    # ── schedule loop ─────────────────────────────────────────────────────────

    def _schedule_loop(self) -> None:
        while self._active:
            now  = datetime.now()
            today = now.strftime("%Y-%m-%d")

            # Daily scheduled run
            if (
                now.hour == self._hour
                and self._last_summary_date != today
            ):
                self._run()
                self._last_summary_date = today

            # Threshold-triggered run
            elif self._last_summary_date != today:
                try:
                    conn = self._memory._read_conn()
                    count = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
                    if count >= self.EPISODE_THRESHOLD:
                        log.info(f"Summarizer: threshold triggered ({count} episodes)")
                        self._run()
                        self._last_summary_date = today
                except Exception:
                    pass

            time.sleep(60)   # check every minute

    # ── main summarization ────────────────────────────────────────────────────

    def _run(self, lookback_h: int = 24) -> str:
        cutoff = time.time() - lookback_h * 3600
        episodes = self._load_recent_episodes(cutoff)

        if not episodes:
            log.info("Summarizer: no episodes to compress")
            return "Nessun episodio da riassumere."

        log.info(f"Summarizer: compressing {len(episodes)} episodes from last {lookback_h}h")

        # Group by person
        by_person: Dict[str, List[Dict]] = defaultdict(list)
        general: List[Dict] = []
        for ep in episodes:
            who = ep.get("who", "unknown")
            if who and who != "unknown":
                by_person[who].append(ep)
            else:
                general.append(ep)

        summaries: List[str] = []

        # Per-person summaries
        for person_id, eps in by_person.items():
            s = self._summarize_person(person_id, eps)
            if s:
                summaries.append(s)
                self._memory.upsert_fact(
                    f"daily_summary_{person_id}_{datetime.now().strftime('%Y%m%d')}",
                    s, confidence=0.85
                )

        # General activity summary
        if general:
            s = self._summarize_general(general)
            if s:
                summaries.append(s)

        # Combined daily fact
        combined = " ".join(summaries) if summaries else "Giornata tranquilla."
        date_str = datetime.now().strftime("%Y-%m-%d")
        self._memory.upsert_fact(f"daily_summary_{date_str}", combined, confidence=0.9)

        # Store as episode
        self._memory.add_episode(
            what=f"Riassunto giornaliero: {combined[:100]}",
            action="summarize",
            outcome="success",
        )

        log.info(f"Summarizer: done → {combined[:80]}…")
        return combined

    # ── per-person summary ────────────────────────────────────────────────────

    def _summarize_person(self, person_id: str, episodes: List[Dict]) -> str:
        profile = self._memory.get_person(person_id)
        name    = (profile or {}).get("display_name", person_id)
        n       = len(episodes)
        actions = [ep.get("action", "") for ep in episodes]
        action_counts: Dict[str, int] = {}
        for a in actions:
            action_counts[a] = action_counts.get(a, 0) + 1

        # Try LLM for richer summary if available
        llm_summary = self._llm_summarize(episodes, context=f"persona: {name}")
        if llm_summary:
            return llm_summary

        # Template fallback
        parts = [f"{name}: {n} interazioni."]
        if action_counts.get("greet", 0):
            parts.append(f"Salutato {action_counts['greet']} volt{'a' if action_counts['greet'] == 1 else 'e'}.")
        if action_counts.get("respond", 0):
            parts.append(f"{action_counts['respond']} risposte fornite.")
        return " ".join(parts)

    def _summarize_general(self, episodes: List[Dict]) -> str:
        n = len(episodes)
        types = set(ep.get("action", "") for ep in episodes)

        llm_summary = self._llm_summarize(episodes[:10], context="eventi generali")
        if llm_summary:
            return llm_summary

        return f"Attività generale: {n} eventi ({', '.join(t for t in types if t)})."

    # ── LLM summarization (optional) ──────────────────────────────────────────

    def _llm_summarize(self, episodes: List[Dict], context: str = "") -> Optional[str]:
        if not self._llm_model:
            return None
        if _OLLAMA_LOCK and not _OLLAMA_LOCK.acquire(blocking=False):
            log.debug("Summarizer: ollama busy — using template")
            return None
        try:
            import ollama
            episode_text = "\n".join(
                f"- [{ep.get('action','')}] {ep.get('what','')}"
                for ep in episodes[:15]
            )
            prompt = (
                f"Riassumi questi eventi del robot Spooky in 1-2 frasi italiane concise. "
                f"Contesto: {context}.\n\nEventi:\n{episode_text}"
            )
            resp = ollama.chat(
                model=self._llm_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 80},
                keep_alive=0,
            )
            text = resp.message.content.strip() if hasattr(resp, "message") \
                   else resp["message"]["content"].strip()
            return text if text else None
        except Exception as e:
            log.warning(f"Summarizer LLM: {e}")
            return None
        finally:
            if _OLLAMA_LOCK:
                _OLLAMA_LOCK.release()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _load_recent_episodes(self, since_ts: float) -> List[Dict]:
        try:
            conn = self._memory._read_conn()
            rows = conn.execute(
                "SELECT * FROM episodic WHERE ts > ? ORDER BY ts ASC",
                (since_ts,)
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error(f"Summarizer: load error — {e}")
            return []

    def __repr__(self) -> str:
        return (
            f"<Summarizer hour={self._hour:02d}:00 "
            f"last={self._last_summary_date} "
            f"llm={self._llm_model}>"
        )
