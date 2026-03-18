"""
services/choreography.py — Named animation sequences for the robot body.

Each sequence is a list of steps:
    (action_name, kwargs, duration_s)

Actions map to MotorService methods.
Sequences run in a daemon thread and are interruptible.

Usage:
    choreo = Choreography(motor)
    choreo.play("excited")       # non-blocking
    choreo.play("thinking", wait=True)
    choreo.stop()
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


# ── Sequence definitions ──────────────────────────────────────────────────────
#
# Each step: (method_name_on_motor, {kwargs}, duration_s)
# If method_name is None → just sleep for duration_s (pause between moves)

Step = Tuple[Optional[str], Dict[str, Any], float]

_SEQUENCES: Dict[str, List[Step]] = {

    "excited": [
        ("look_at",   {"pan":  30, "tilt":  0},  0.15),
        ("look_at",   {"pan": -30, "tilt":  0},  0.15),
        ("look_at",   {"pan":  30, "tilt":  0},  0.15),
        ("look_at",   {"pan": -30, "tilt":  0},  0.15),
        ("look_at",   {"pan":   0, "tilt":  0},  0.20),
        ("wave",      {},                         1.20),
        ("look_at",   {"pan":   0, "tilt":  0},  0.10),
    ],

    "thinking": [
        ("look_at",   {"pan": -20, "tilt": -10},  0.6),
        (None,        {},                          0.8),
        ("look_at",   {"pan":  20, "tilt":  10},  0.5),
        (None,        {},                          0.5),
        ("look_at",   {"pan":   0, "tilt":   0},  0.3),
    ],

    "curious": [
        ("look_at",   {"pan":  0, "tilt":  15},  0.4),
        ("look_at",   {"pan": 15, "tilt":  10},  0.4),
        ("look_at",   {"pan": -15, "tilt": 10},  0.4),
        ("look_at",   {"pan":  0, "tilt":   0},  0.3),
    ],

    "greeting": [
        ("look_at",   {"pan":  0, "tilt":  10},  0.3),
        ("wave",      {},                         1.00),
        ("look_at",   {"pan":  0, "tilt":   0},  0.2),
    ],

    "nod": [
        ("look_at",   {"pan": 0, "tilt":  15},  0.25),
        ("look_at",   {"pan": 0, "tilt":  -5},  0.25),
        ("look_at",   {"pan": 0, "tilt":  15},  0.25),
        ("look_at",   {"pan": 0, "tilt":   0},  0.20),
    ],

    "shake_head": [
        ("look_at",   {"pan":  30, "tilt": 0},  0.25),
        ("look_at",   {"pan": -30, "tilt": 0},  0.25),
        ("look_at",   {"pan":  30, "tilt": 0},  0.25),
        ("look_at",   {"pan":   0, "tilt": 0},  0.20),
    ],

    "shy": [
        ("look_at",   {"pan": -40, "tilt": -15},  0.5),
        (None,        {},                           0.8),
        ("look_at",   {"pan":   0, "tilt":   0},   0.4),
    ],

    "alert": [
        ("look_at",   {"pan":  0, "tilt":  5},  0.2),
        ("look_at",   {"pan": 30, "tilt":  0},  0.3),
        ("look_at",   {"pan":-30, "tilt":  0},  0.3),
        ("look_at",   {"pan":  0, "tilt":  0},  0.2),
    ],

    "look_around": [
        ("look_at",   {"pan": -50, "tilt":  5},  0.7),
        ("look_at",   {"pan":   0, "tilt":  0},  0.4),
        ("look_at",   {"pan":  50, "tilt":  5},  0.7),
        ("look_at",   {"pan":   0, "tilt": -5},  0.4),
        ("look_at",   {"pan":   0, "tilt":  0},  0.3),
    ],

    "sleep_pose": [
        ("look_at",   {"pan": 0, "tilt": -25},  0.5),
    ],

    "wake_up": [
        ("look_at",   {"pan": 0, "tilt": -25},  0.2),
        ("look_at",   {"pan": 0, "tilt":  10},  0.5),
        ("look_at",   {"pan": 0, "tilt":   0},  0.3),
    ],

    "patrol_turn": [
        ("turn_right", {"speed": 30},  0.5),
        ("stop",       {},             0.2),
        ("turn_left",  {"speed": 30},  1.0),
        ("stop",       {},             0.2),
        ("turn_right", {"speed": 30},  0.5),
        ("stop",       {},             0.2),
    ],
}


# ── Choreography ──────────────────────────────────────────────────────────────

class Choreography:
    """
    Plays named animation sequences on the robot.

    Thread-safe: a new play() call cancels any running sequence first.
    """

    def __init__(self, motor):
        self._motor  = motor
        self._stop_ev = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock   = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────────

    def play(self, name: str, wait: bool = False) -> bool:
        """
        Play a named sequence. Cancels any running sequence first.
        Returns False if sequence name is unknown.
        """
        if name not in _SEQUENCES:
            log.warning(f"Choreography: unknown sequence '{name}'")
            return False

        self.stop()   # cancel current if any
        with self._lock:
            self._stop_ev.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(_SEQUENCES[name], name),
                daemon=True,
                name=f"Choreo-{name}",
            )
            self._thread.start()

        if wait:
            self._thread.join()
        return True

    def stop(self) -> None:
        """Request cancellation and wait for thread to finish (max 2s)."""
        self._stop_ev.set()
        with self._lock:
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def available(self) -> List[str]:
        return list(_SEQUENCES.keys())

    # ── internals ─────────────────────────────────────────────────────────────

    def _run(self, steps: List[Step], name: str) -> None:
        log.debug(f"Choreo: START '{name}'")
        for method, kwargs, duration in steps:
            if self._stop_ev.is_set():
                break
            if method is not None:
                fn = getattr(self._motor, method, None)
                if fn:
                    try:
                        fn(**kwargs)
                    except Exception as e:
                        log.error(f"Choreo step {method}: {e}")
            # Interruptible sleep
            self._stop_ev.wait(timeout=duration)
        log.debug(f"Choreo: END '{name}'")

    def __repr__(self) -> str:
        return f"<Choreography playing={self.is_playing}>"
