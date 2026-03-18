"""
services/alert.py — Alert service for Night Watch mode.

Implements a four-level alert system with event fusion to reduce false positives.
External adapters (Telegram, webhook, Home Assistant) are optional and
loaded dynamically — core module has no cloud dependency.

Alert levels:
  L0  → log only
  L1  → orient camera, classify
  L2  → quiet local alert (optional sound/TTS), save snapshot metadata
  L3  → external notification via registered adapter
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

from core.bus import EventBus, EventType
from core.modes import Mode

log = logging.getLogger(__name__)


# ── Fusion config ─────────────────────────────────────────────────────────────

@dataclass
class FusionConfig:
    # Confidence thresholds
    person_confidence_min: float = 0.65    # below this → treat as unknown
    # Cooldown between same-level alerts (seconds)
    cooldown_l1: float = 30.0
    cooldown_l2: float = 60.0
    cooldown_l3: float = 300.0
    # Number of motion events within burst_window_s to trigger "burst"
    burst_count: int    = 3
    burst_window_s: float = 20.0
    # Max events kept in fusion window
    fusion_window_s: float = 60.0


# ── Alert record ──────────────────────────────────────────────────────────────

@dataclass
class AlertRecord:
    level: int
    reason: str
    ts: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)


# ── External adapter protocol ─────────────────────────────────────────────────

class AlertAdapter:
    """Base class for external alert sinks. Subclass to add Telegram/webhook/HA."""

    def send(self, level: int, reason: str, payload: Dict[str, Any]) -> None:
        raise NotImplementedError


# ── AlertService ──────────────────────────────────────────────────────────────

class AlertService:
    """
    Event fusion engine + alert dispatcher.

    Subscribes to perception events. Accumulates evidence within a rolling
    time window. Escalates only when combined evidence exceeds a threshold.
    """

    def __init__(
        self,
        bus: EventBus,
        mode_getter: Callable[[], Mode],
        config: Optional[FusionConfig] = None,
    ):
        self._bus         = bus
        self._mode_getter = mode_getter
        self._cfg         = config or FusionConfig()
        self._adapters:   List[AlertAdapter] = []
        self._history:    Deque[AlertRecord] = deque(maxlen=200)

        # Fusion event buffer: (event_type, ts)
        self._events: Deque[tuple[str, float]] = deque(maxlen=500)

        # Last-raised timestamps per level
        self._last_raised: Dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0}

        # Subscribe to relevant perception events
        for et in (
            EventType.MOTION_DETECTED,
            EventType.PERSON_DETECTED,
            EventType.PERSON_IDENTIFIED,
            EventType.PERSON_UNKNOWN,
            EventType.UNUSUAL_SOUND,
            EventType.DESK_DISTURBANCE,
        ):
            bus.subscribe(et, self._on_event)

    # ── adapter registration ──────────────────────────────────────────────────

    def register_adapter(self, adapter: AlertAdapter) -> None:
        self._adapters.append(adapter)
        log.info(f"AlertService: registered adapter {type(adapter).__name__}")

    # ── event ingestion ───────────────────────────────────────────────────────

    def _on_event(self, ev) -> None:
        # Only fuse events in night_watch mode
        if self._mode_getter() is not Mode.NIGHT_WATCH:
            return

        now = time.time()
        self._events.append((ev.type, now))
        self._flush_old(now)
        self._fuse(ev, now)

    def _flush_old(self, now: float) -> None:
        cutoff = now - self._cfg.fusion_window_s
        while self._events and self._events[0][1] < cutoff:
            self._events.popleft()

    def _fuse(self, ev, now: float) -> None:
        event_types = [e[0] for e in self._events]

        # L3: unknown person + motion + sound
        if (
            EventType.PERSON_UNKNOWN in event_types
            and EventType.MOTION_DETECTED in event_types
            and EventType.UNUSUAL_SOUND in event_types
        ):
            self._raise(3, "unknown_person+motion+sound", ev.payload, now)
            return

        # L2: unknown person + any other event, or repeated burst
        if EventType.PERSON_UNKNOWN in event_types and len(event_types) >= 2:
            self._raise(2, "unknown_person+event", ev.payload, now)
            return

        burst = self._count_burst(EventType.MOTION_DETECTED, now)
        if burst >= self._cfg.burst_count:
            self._raise(2, f"motion_burst_{burst}", ev.payload, now)
            return

        # L1: known person detected OR single motion
        if EventType.PERSON_DETECTED in event_types:
            self._raise(1, "person_detected", ev.payload, now)
            return
        if EventType.MOTION_DETECTED in event_types:
            self._raise(1, "motion_detected", ev.payload, now)

    def _count_burst(self, event_type: str, now: float) -> int:
        cutoff = now - self._cfg.burst_window_s
        return sum(1 for et, ts in self._events if et == event_type and ts >= cutoff)

    # ── alert dispatch ────────────────────────────────────────────────────────

    def _raise(
        self, level: int, reason: str, payload: Dict[str, Any], now: float
    ) -> None:
        cooldown = getattr(self._cfg, f"cooldown_l{level}", 30.0)
        if now - self._last_raised.get(level, 0) < cooldown:
            return

        self._last_raised[level] = now
        record = AlertRecord(level=level, reason=reason, payload=payload, ts=now)
        self._history.append(record)

        log.info(f"ALERT L{level}: {reason} | {payload}")
        self._bus.publish(
            EventType.ALERT_RAISED,
            {"level": level, "reason": reason, **payload},
            source="AlertService",
        )

        if level >= 3:
            for adapter in self._adapters:
                try:
                    adapter.send(level, reason, payload)
                except Exception as e:
                    log.error(f"AlertAdapter error: {e}")

    # ── manual trigger ────────────────────────────────────────────────────────

    def raise_manual(self, level: int, reason: str, payload: Optional[Dict] = None) -> None:
        """Raise an alert from outside the fusion engine (e.g. from CLI)."""
        self._raise(level, reason, payload or {}, time.time())

    def clear(self) -> None:
        self._events.clear()
        self._bus.publish(EventType.ALERT_CLEARED, {}, source="AlertService")

    # ── history ───────────────────────────────────────────────────────────────

    def recent_alerts(self, n: int = 20) -> List[AlertRecord]:
        return list(self._history)[-n:]

    def __repr__(self) -> str:
        return (
            f"<AlertService adapters={len(self._adapters)} "
            f"fused_events={len(self._events)} "
            f"history={len(self._history)}>"
        )
