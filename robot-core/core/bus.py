"""
core/bus.py — Thread-safe pub/sub event bus.

All inter-service communication goes through here.
No service imports another service directly.

Usage:
    bus = EventBus()
    bus.start()

    bus.subscribe(EventType.PERSON_DETECTED, my_handler)
    bus.publish(EventType.PERSON_DETECTED, {"person_id": "alice", "confidence": 0.92})
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# ── Canonical event type strings ────────────────────────────────────────────

class EventType:
    # Perception
    PERSON_DETECTED         = "person_detected"
    PERSON_IDENTIFIED       = "person_identified"
    PERSON_UNKNOWN          = "person_unknown"
    PERSON_LOST             = "person_lost"
    MOTION_DETECTED         = "motion_detected"
    UNUSUAL_SOUND           = "unusual_sound_detected"
    DESK_DISTURBANCE        = "desk_disturbance_detected"
    SCENE_ANALYZED          = "scene_analyzed"
    OBJECTS_DETECTED        = "objects_detected"

    # Audio / voice
    WAKE_WORD_DETECTED      = "wake_word_detected"
    SPEECH_TRANSCRIBED      = "speech_transcribed"
    COMMAND_PARSED          = "command_parsed"
    TTS_STARTED             = "tts_started"
    TTS_FINISHED            = "tts_finished"

    # Safety / hardware
    OBSTACLE_DETECTED       = "obstacle_detected"
    OBSTACLE_CLEARED        = "obstacle_cleared"
    SAFETY_FAULT            = "safety_fault"
    OVERTEMP                = "overtemp"
    LOW_MEMORY              = "low_memory"
    ACTUATOR_ERROR          = "actuator_error"

    # Mode
    MODE_CHANGE_REQUEST     = "mode_change_request"
    MODE_CHANGED            = "mode_changed"

    # Memory / learning
    MEMORY_CANDIDATE        = "memory_candidate_created"
    MEMORY_PROMOTED         = "memory_promoted"
    MEMORY_FORGOTTEN        = "memory_forgotten"

    # Skills / mind
    SKILL_STARTED           = "skill_started"
    SKILL_FINISHED          = "skill_finished"
    GOAL_SET                = "goal_set"
    GOAL_CLEARED            = "goal_cleared"

    # Experiments
    EXPERIMENT_STARTED      = "experiment_started"
    EXPERIMENT_FINISHED     = "experiment_finished"

    # Alerts (night watch)
    ALERT_RAISED            = "alert_raised"
    ALERT_CLEARED           = "alert_cleared"

    # Audio state
    MIC_STATE_CHANGED       = "mic_state_changed"   # {"state": "idle"|"listening"|"thinking"}

    # Environment scan
    SCAN_COMPLETE           = "scan_complete"        # {"readings": [{"angle":0,"dist":99.0}, ...]}

    # System
    HEARTBEAT               = "heartbeat"
    SHUTDOWN_REQUEST        = "shutdown_request"


# ── Event dataclass ──────────────────────────────────────────────────────────

@dataclass
class Event:
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.time)

    def get(self, key: str, default: Any = None) -> Any:
        return self.payload.get(key, default)


EventHandler = Callable[[Event], None]


# ── EventBus ─────────────────────────────────────────────────────────────────

class EventBus:
    """
    Asynchronous, thread-safe pub/sub bus.

    Events are dispatched from a single worker thread to avoid handler
    re-entrancy issues. Handlers that block will delay other events;
    use threading inside handlers for heavy work.
    """

    WILDCARD = "*"

    def __init__(self, max_queue: int = 1024):
        self._handlers: Dict[str, List[EventHandler]] = {}
        self._wildcard: List[EventHandler] = []
        self._queue: queue.Queue[Optional[Event]] = queue.Queue(maxsize=max_queue)
        self._lock = threading.RLock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._dropped = 0

    # ── subscription ────────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            if event_type == self.WILDCARD:
                if handler not in self._wildcard:
                    self._wildcard.append(handler)
            else:
                bucket = self._handlers.setdefault(event_type, [])
                if handler not in bucket:
                    bucket.append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            if event_type == self.WILDCARD:
                self._wildcard = [h for h in self._wildcard if h is not handler]
            else:
                bucket = self._handlers.get(event_type, [])
                self._handlers[event_type] = [h for h in bucket if h is not handler]

    # ── publishing ──────────────────────────────────────────────────────────

    def publish(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> None:
        ev = Event(type=event_type, payload=payload or {}, source=source)
        try:
            self._queue.put_nowait(ev)
        except queue.Full:
            self._dropped += 1
            if self._dropped % 50 == 1:
                log.warning(
                    f"EventBus: queue full — dropped {self._dropped} events so far "
                    f"(latest: {event_type})"
                )

    def publish_sync(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "",
    ) -> None:
        """Dispatch immediately in the calling thread (use only for shutdown/faults)."""
        ev = Event(type=event_type, payload=payload or {}, source=source)
        self._dispatch(ev)

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="EventBus"
        )
        self._thread.start()
        log.info("EventBus started")

    def stop(self, timeout: float = 3.0) -> None:
        self._running = False
        self._queue.put(None)  # sentinel
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info(f"EventBus stopped (dropped total: {self._dropped})")

    # ── internals ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while self._running:
            try:
                ev = self._queue.get(timeout=1.0)
                if ev is None:
                    break
                self._dispatch(ev)
            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"EventBus._run: {e}")

    def _dispatch(self, ev: Event) -> None:
        with self._lock:
            handlers = list(self._handlers.get(ev.type, []))
            wildcards = list(self._wildcard)
        for handler in handlers + wildcards:
            try:
                handler(ev)
            except Exception as e:
                log.error(f"EventBus handler error [{ev.type}] in {handler}: {e}")

    def __repr__(self) -> str:
        n = sum(len(v) for v in self._handlers.values()) + len(self._wildcard)
        return f"<EventBus handlers={n} qsize={self._queue.qsize()} dropped={self._dropped}>"
