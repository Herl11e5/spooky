"""
skills/track_face.py — Smooth face-tracking with the robot's head.

Subscribes to PERSON_DETECTED events and nudges the head toward the
face centre using a proportional controller.
Stops automatically when PERSON_LOST fires or mode changes.

Proportional gain P = 0.12 (tunable; stored in procedural memory).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from core.safety import SafetyMonitor
from services.motor import MotorService
from services.memory import MemoryService
from skills.base import Skill

log = logging.getLogger(__name__)


class TrackFaceSkill(Skill):
    """
    Reactive face-tracking.

    Unlike most skills, this one works as a persistent subscriber rather
    than a one-shot thread. It registers an event handler for
    PERSON_DETECTED and updates the head on every frame that contains a face.

    The _run() method just blocks while tracking is active — the actual
    work happens in _on_face().
    """

    name          = "track_face"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.IDLE_OBSERVER, Mode.FOCUS_ASSISTANT})

    # Default proportional gain (overridden by procedural memory if set)
    _DEFAULT_GAIN = 0.12
    # Dead-zone: don't move if face is already near centre (normalised units)
    _DEAD_ZONE = 0.08

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        safety: SafetyMonitor,
        motor: MotorService,
        memory: MemoryService,
    ):
        super().__init__(bus, mode_manager, safety)
        self._motor  = motor
        self._memory = memory
        self._gain: float = memory.get_param("track_face", "gain", self._DEFAULT_GAIN)

        # Register face event handler (always subscribed, gated internally)
        bus.subscribe(EventType.PERSON_DETECTED, self._on_face)
        bus.subscribe(EventType.PERSON_LOST,     self._on_face_lost)
        bus.subscribe(EventType.MODE_CHANGED,    self._on_mode_change)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        """Block until stop() is called. Face updates happen in _on_face()."""
        log.info("TrackFaceSkill: tracking active")
        while not self.should_stop:
            time.sleep(0.1)
        # Return head to center on exit
        self._motor.look_center()
        log.info("TrackFaceSkill: stopped, head centered")

    # ── event-driven updates ──────────────────────────────────────────────────

    def _on_face(self, ev) -> None:
        if not self.is_running:
            return
        if self._mm.current not in self.allowed_modes:
            return

        dx = ev.get("center_x", 0.0)   # -1=left, +1=right
        dy = ev.get("center_y", 0.0)   # -1=top,  +1=bottom

        # Dead zone — no jitter when face is already centred
        if abs(dx) < self._DEAD_ZONE and abs(dy) < self._DEAD_ZONE:
            return

        # Proportional nudge
        self._motor.nudge_toward(dx, dy, gain=self._gain)

    def _on_face_lost(self, ev) -> None:
        if self.is_running:
            self._motor.look_center()

    def _on_mode_change(self, ev) -> None:
        to = ev.get("to", "")
        if to not in {m.value for m in self.allowed_modes}:
            self.stop()

    # ── gain tuning (called by learning layer) ────────────────────────────────

    def set_gain(self, gain: float) -> None:
        self._gain = max(0.01, min(0.5, gain))
        self._memory.set_param("track_face", "gain", self._gain, confidence=0.8)
        log.info(f"TrackFaceSkill: gain updated to {self._gain:.3f}")
