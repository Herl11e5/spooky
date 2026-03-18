"""
services/learning.py — Preference extraction and behavior tuning.

Responsibilities:
  1. Extract candidate facts / preferences from user utterances (regex-first,
     LLM-assisted when available)
  2. Track interaction outcomes → update social profiles + familiarity
  3. Tune procedural parameters within safe bounds
  4. Emit MEMORY_CANDIDATE events so the rest of the system can act on them

Design rules:
  - Never auto-promote a fact on first observation (promote_threshold = 3)
  - Never modify safety-critical parameters
  - Always store baseline before tuning so rollback is possible
  - LLM extraction is optional — regex handles the common Italian patterns
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.bus import EventBus, EventType
from services.memory import MemoryService

log = logging.getLogger(__name__)


# ── Parameter bounds (safe ranges only) ──────────────────────────────────────

_PARAM_BOUNDS: Dict[Tuple[str, str], Tuple[Any, Any]] = {
    ("track_face",  "gain"):            (0.04, 0.40),
    ("mind",        "max_tokens"):      (60,   250),
    ("mind",        "temperature"):     (0.3,  1.0),
    ("idle_behavior","curiosity_threshold"): (0.4, 0.9),
}


# ── Italian preference patterns ───────────────────────────────────────────────

@dataclass
class ExtractionRule:
    pattern:  re.Pattern
    key_tmpl: str     # fact key template (may use group 1 as {value})
    value_tmpl: str   # fact value template (may use group 1 as {value})
    confidence: float = 0.6


_EXTRACTION_RULES: List[ExtractionRule] = [
    # Identity
    ExtractionRule(
        re.compile(r"\bmi chiamo\s+(\w+)", re.IGNORECASE),
        "user_name", "{value}", 0.9,
    ),
    ExtractionRule(
        re.compile(r"\bsono\s+(?:un[ao]?\s+)?(\w+(?:\s+\w+)?)", re.IGNORECASE),
        "user_role", "{value}", 0.6,
    ),
    ExtractionRule(
        re.compile(r"\blavoro\s+(?:come\s+|da\s+)?(\w+(?:\s+\w+)?)", re.IGNORECASE),
        "user_job", "{value}", 0.7,
    ),
    # Preferences
    ExtractionRule(
        re.compile(r"\bmi\s+piace\s+(?:molto\s+)?(.+?)(?:\.|$)", re.IGNORECASE),
        "likes_{value}", "true", 0.65,
    ),
    ExtractionRule(
        re.compile(r"\bnon\s+mi\s+piace\s+(.+?)(?:\.|$)", re.IGNORECASE),
        "dislikes_{value}", "true", 0.65,
    ),
    ExtractionRule(
        re.compile(r"\bpreferisco\s+(.+?)(?:\.|$)", re.IGNORECASE),
        "prefers_{value}", "true", 0.70,
    ),
    # Behavior requests
    ExtractionRule(
        re.compile(r"\brispost[ae]\s+(?:più\s+)?brev[ei]", re.IGNORECASE),
        "prefers_short_responses", "true", 0.80,
    ),
    ExtractionRule(
        re.compile(r"\bnon\s+disturb(?:armi|arci|are)", re.IGNORECASE),
        "prefers_minimal_interruptions", "true", 0.85,
    ),
    ExtractionRule(
        re.compile(r"\bparla\s+(?:più\s+)?lentamente", re.IGNORECASE),
        "prefers_slow_speech", "true", 0.80,
    ),
]


# ── CandidateFact ─────────────────────────────────────────────────────────────

@dataclass
class CandidateFact:
    key:        str
    value:      str
    confidence: float
    source:     str    # "regex" | "llm"
    raw_text:   str


# ── LearningService ───────────────────────────────────────────────────────────

class LearningService:
    """
    Extracts facts from conversations and tunes behavior parameters.
    Runs extraction in background threads — never blocks the caller.
    """

    def __init__(self, bus: EventBus, memory: MemoryService, cfg):
        self._bus    = bus
        self._memory = memory
        self._cfg    = cfg

        # Interaction outcome tracking
        self._interaction_counts: Dict[str, int] = {}   # person_id → count
        self._engagement_score:   Dict[str, float] = {}  # person_id → 0-1

        # Subscribe to relevant events
        bus.subscribe(EventType.COMMAND_PARSED,    self._on_command)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_identified)
        bus.subscribe(EventType.TTS_FINISHED,      self._on_tts_finished)

        self._current_person: Optional[str] = None
        self._session_start: float = time.time()
        self._session_commands: int = 0

    # ── public API ────────────────────────────────────────────────────────────

    def extract_from_text(self, text: str, person_id: Optional[str] = None) -> List[CandidateFact]:
        """
        Synchronously extract candidate facts from a user utterance.
        Returns list of CandidateFact (not yet stored).
        """
        facts = self._regex_extract(text)
        for f in facts:
            log.info(f"Learning: candidate fact [{f.source}] {f.key}={f.value} ({f.confidence:.0%})")
            # Store as candidate; MemoryService promotes after threshold repetitions
            self._memory.upsert_fact(f.key, f.value, confidence=f.confidence)
            self._bus.publish(
                EventType.MEMORY_CANDIDATE,
                {"key": f.key, "value": f.value, "confidence": f.confidence},
                source="LearningService",
            )
        return facts

    def record_engagement(self, person_id: str, engaged: bool) -> None:
        """
        Call after each interaction to update engagement tracking.
        engaged=True when user replied or showed positive reaction.
        """
        count = self._interaction_counts.get(person_id, 0) + 1
        self._interaction_counts[person_id] = count
        score = self._engagement_score.get(person_id, 0.5)
        # Exponential moving average
        new_score = score * 0.8 + (0.2 if engaged else 0.0)
        self._engagement_score[person_id] = new_score
        # Boost familiarity on engagement
        delta = 0.05 if engaged else 0.01
        self._memory.upsert_person(person_id, person_id, familiarity_delta=delta)

    def tune_parameter(
        self,
        skill: str,
        key: str,
        new_value: Any,
        confidence: float = 0.6,
    ) -> bool:
        """
        Safely update a procedural parameter within its defined bounds.
        Returns False if the value is out of bounds or the param is unknown.
        """
        bounds = _PARAM_BOUNDS.get((skill, key))
        if bounds is None:
            log.warning(f"Learning: no bounds defined for ({skill}, {key}) — refusing tune")
            return False
        lo, hi = bounds
        if not (lo <= new_value <= hi):
            log.warning(f"Learning: {skill}.{key}={new_value} out of bounds [{lo},{hi}]")
            return False
        self._memory.set_param(skill, key, new_value, confidence=confidence)
        log.info(f"Learning: tuned {skill}.{key} = {new_value} (confidence={confidence:.0%})")
        return True

    def apply_behavior_preferences(self) -> None:
        """
        Read promoted semantic facts and apply matching procedural param changes.
        Call on startup and after MEMORY_PROMOTED events.
        """
        mappings = {
            "prefers_short_responses":       ("mind", "max_tokens", 90),
            "prefers_minimal_interruptions": ("idle_behavior", "curiosity_threshold", 0.80),
        }
        for fact_key, (skill, param, value) in mappings.items():
            stored = self._memory.get_fact(fact_key)
            if stored == "true":
                self.tune_parameter(skill, param, value, confidence=0.75)

    def engagement_score(self, person_id: str) -> float:
        return self._engagement_score.get(person_id, 0.5)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_command(self, ev) -> None:
        cmd = ev.get("command", "")
        if not cmd:
            return
        self._session_commands += 1
        # Extract in background thread (may call LLM later)
        threading.Thread(
            target=self.extract_from_text,
            args=(cmd, self._current_person),
            daemon=True,
            name="Learn-Extract",
        ).start()

    def _on_person_identified(self, ev) -> None:
        self._current_person = ev.get("person_id")

    def _on_tts_finished(self, ev) -> None:
        # When TTS finishes, consider the exchange "engaged" if a command
        # was issued in the last 30s (rough proxy)
        if self._current_person:
            recently_active = self._session_commands > 0
            self.record_engagement(self._current_person, engaged=recently_active)

    # ── extraction ────────────────────────────────────────────────────────────

    def _regex_extract(self, text: str) -> List[CandidateFact]:
        facts: List[CandidateFact] = []
        for rule in _EXTRACTION_RULES:
            m = rule.pattern.search(text)
            if not m:
                continue
            captured = m.group(1).strip() if m.lastindex else ""
            # Normalise: lowercase, max 30 chars, no spaces → underscores in key
            captured_safe = re.sub(r"\s+", "_", captured.lower())[:30]
            key   = rule.key_tmpl.replace("{value}", captured_safe)
            value = rule.value_tmpl.replace("{value}", captured)
            facts.append(CandidateFact(
                key=key, value=value,
                confidence=rule.confidence,
                source="regex",
                raw_text=text,
            ))
        return facts

    def session_summary(self) -> Dict[str, Any]:
        return {
            "duration_s":        round(time.time() - self._session_start),
            "commands":          self._session_commands,
            "known_persons":     len(self._interaction_counts),
            "engagement_scores": {k: round(v, 3) for k, v in self._engagement_score.items()},
        }

    def __repr__(self) -> str:
        return (
            f"<LearningService persons={len(self._interaction_counts)} "
            f"commands={self._session_commands}>"
        )
