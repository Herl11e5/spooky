"""
core/modes.py — Mode state machine.

Modes represent the robot's overall behavioral stance. Only one mode is
active at a time. Transitions are validated against an allowed-edges table.

Usage:
    mm = ModeManager(bus)
    mm.request_transition(Mode.NIGHT_WATCH)  # validated, publishes MODE_CHANGED
    mm.current                               # → Mode.NIGHT_WATCH
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Callable, Dict, Optional, Set

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)


# ── Mode enum ─────────────────────────────────────────────────────────────────

class Mode(str, Enum):
    COMPANION_DAY    = "companion_day"
    FOCUS_ASSISTANT  = "focus_assistant"
    IDLE_OBSERVER    = "idle_observer"
    NIGHT_WATCH      = "night_watch"
    SAFE_SHUTDOWN    = "safe_shutdown"


# ── Allowed transitions ───────────────────────────────────────────────────────
#
#  Key = current mode, Value = set of modes it may transition to.
#  safe_shutdown is a terminal state; no transitions out.
#  Any mode can reach safe_shutdown (added programmatically below).

_EDGES: Dict[Mode, Set[Mode]] = {
    Mode.COMPANION_DAY:   {Mode.FOCUS_ASSISTANT, Mode.IDLE_OBSERVER, Mode.NIGHT_WATCH},
    Mode.FOCUS_ASSISTANT: {Mode.COMPANION_DAY},
    Mode.IDLE_OBSERVER:   {Mode.COMPANION_DAY},
    Mode.NIGHT_WATCH:     {Mode.COMPANION_DAY},
    Mode.SAFE_SHUTDOWN:   set(),
}
# Inject safe_shutdown as reachable from every mode
for _mode in list(_EDGES.keys()):
    if _mode is not Mode.SAFE_SHUTDOWN:
        _EDGES[_mode].add(Mode.SAFE_SHUTDOWN)


# ── ModeManager ───────────────────────────────────────────────────────────────

class ModeManager:
    """
    Validates and applies mode transitions.

    Thread-safe. Publishes EventType.MODE_CHANGED on every successful transition.
    Subscribers can gate behaviour on the current mode without importing this class.
    """

    def __init__(self, bus: EventBus, initial: Mode = Mode.COMPANION_DAY):
        self._bus  = bus
        self._mode = initial
        self._lock = threading.Lock()
        self._entered_at: float = time.time()
        self._history: list[tuple[float, Mode, Mode]] = []   # (ts, from, to)

        # Listen for requests from any service/skill
        bus.subscribe(EventType.MODE_CHANGE_REQUEST, self._on_request)
        # Safety faults force shutdown immediately
        bus.subscribe(EventType.SAFETY_FAULT, self._on_fault)
        bus.subscribe(EventType.OVERTEMP,     self._on_fault)

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def current(self) -> Mode:
        return self._mode

    @property
    def time_in_mode(self) -> float:
        return time.time() - self._entered_at

    def can_transition(self, target: Mode) -> bool:
        return target in _EDGES.get(self._mode, set())

    def request_transition(self, target: Mode, reason: str = "") -> bool:
        """
        Attempt a transition to `target`.
        Returns True if the transition was applied, False if denied.
        """
        with self._lock:
            if target == self._mode:
                return True
            if not self.can_transition(target):
                log.warning(
                    f"Mode transition denied: {self._mode.value} → {target.value} "
                    f"(reason: {reason or 'not in allowed edges'})"
                )
                return False
            self._apply(target, reason)
            return True

    def force_shutdown(self, reason: str = "") -> None:
        """Bypass edge check — use only for critical faults."""
        with self._lock:
            self._apply(Mode.SAFE_SHUTDOWN, reason or "forced")

    # ── internals ─────────────────────────────────────────────────────────────

    def _apply(self, target: Mode, reason: str) -> None:
        prev = self._mode
        self._mode = target
        self._entered_at = time.time()
        self._history.append((self._entered_at, prev, target))
        log.info(f"Mode: {prev.value} → {target.value}  ({reason})")
        self._bus.publish(
            EventType.MODE_CHANGED,
            {
                "from":   prev.value,
                "to":     target.value,
                "reason": reason,
                "ts":     self._entered_at,
            },
            source="ModeManager",
        )

    def _on_request(self, ev) -> None:
        target_str = ev.get("mode", "")
        try:
            target = Mode(target_str)
        except ValueError:
            log.error(f"ModeManager: unknown mode requested '{target_str}'")
            return
        self.request_transition(target, reason=ev.get("reason", "bus request"))

    def _on_fault(self, ev) -> None:
        self.force_shutdown(reason=ev.get("reason", ev.type))

    # ── helpers ───────────────────────────────────────────────────────────────

    def is_day_mode(self) -> bool:
        return self._mode in (Mode.COMPANION_DAY, Mode.FOCUS_ASSISTANT, Mode.IDLE_OBSERVER)

    def is_night_mode(self) -> bool:
        return self._mode is Mode.NIGHT_WATCH

    def is_active(self) -> bool:
        return self._mode is not Mode.SAFE_SHUTDOWN

    def recent_history(self, n: int = 10) -> list:
        return self._history[-n:]

    def __repr__(self) -> str:
        return f"<ModeManager current={self._mode.value} in_mode={self.time_in_mode:.0f}s>"


# ── Convenience decorator ─────────────────────────────────────────────────────

def only_in(*modes: Mode):
    """
    Decorator that skips a method if the mode manager's current mode
    is not in the allowed set. Requires the host object to have a
    ``mode_manager`` attribute.

    Example:
        @only_in(Mode.COMPANION_DAY, Mode.IDLE_OBSERVER)
        def greet(self):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(self, *args, **kwargs):
            mm: ModeManager = getattr(self, "mode_manager", None)
            if mm is None or mm.current in modes:
                return fn(self, *args, **kwargs)
            log.debug(f"{fn.__qualname__} skipped (mode={mm.current.value})")
        return wrapper
    return decorator
