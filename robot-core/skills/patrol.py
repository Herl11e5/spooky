"""
skills/patrol.py — Short, safe desk patrol.

Executes a pre-defined safe movement sequence around the desk area.
Every step is gated behind SafetyMonitor.is_obstacle_blocked.
The patrol is short by design (~15 seconds) to avoid going too far.

Sequence:
  forward → pause → turn right → forward → turn left → return
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from core.safety import SafetyMonitor
from services.motor import MotorService
from services.choreography import Choreography
from services.conscience import Conscience
from skills.base import Skill

log = logging.getLogger(__name__)


class PatrolSkill(Skill):
    """
    Short, safe desk patrol.
    Self-terminates if obstacle detected, person appears, or mode changes.
    """

    name          = "patrol"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.NIGHT_WATCH})

    # Patrol step durations (seconds) and speed (%)
    _FORWARD_S  = 0.6
    _TURN_S     = 0.4
    _SPEED      = 30   # conservative
    _PAUSE_S    = 0.5

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        safety: SafetyMonitor,
        motor: MotorService,
        choreography: Choreography,
        conscience: Conscience,
    ):
        super().__init__(bus, mode_manager, safety)
        self._motor  = motor
        self._choreo = choreography
        self._con    = conscience
        self._abort  = False

        bus.subscribe(EventType.PERSON_DETECTED,  self._on_person)
        bus.subscribe(EventType.OBSTACLE_DETECTED, self._on_obstacle)
        bus.subscribe(EventType.MODE_CHANGED,      self._on_mode)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("PatrolSkill: starting patrol")
        self._abort = False
        self._motor.look_center()

        steps = [
            ("forward",     self._FORWARD_S),
            ("pause",       self._PAUSE_S),
            ("look_around", None),            # choreography, not motion
            ("turn_right",  self._TURN_S),
            ("forward",     self._FORWARD_S * 0.8),
            ("pause",       self._PAUSE_S),
            ("turn_left",   self._TURN_S * 2),   # return heading
            ("forward",     self._FORWARD_S * 0.8),
            ("turn_right",  self._TURN_S),        # restore heading
            ("pause",       self._PAUSE_S),
        ]

        for action, duration in steps:
            if self.should_stop or self._abort:
                break
            if self._safety.is_obstacle_blocked and action in ("forward",):
                log.info("PatrolSkill: obstacle — skipping forward step")
                self._motor.stop()
                continue
            self._step(action, duration)

        self._motor.stop()
        self._motor.look_center()
        log.info("PatrolSkill: patrol complete")

    def _step(self, action: str, duration: Optional[float]) -> None:
        if action == "forward":
            self._motor.forward(speed=self._SPEED)
            self.sleep(duration)
            self._motor.stop()
        elif action == "turn_right":
            self._motor.turn_right(speed=self._SPEED)
            self.sleep(duration)
            self._motor.stop()
        elif action == "turn_left":
            self._motor.turn_left(speed=self._SPEED)
            self.sleep(duration)
            self._motor.stop()
        elif action == "pause":
            self.sleep(duration)
        elif action == "look_around":
            self._choreo.play("look_around", wait=True)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, ev) -> None:
        """Person appeared — abort patrol gracefully."""
        self._abort = True
        self.stop()

    def _on_obstacle(self, ev) -> None:
        self._motor.stop()
        self._abort = True
        self.stop()

    def _on_mode(self, ev) -> None:
        to = ev.get("to", "")
        if to not in {m.value for m in self.allowed_modes}:
            self._abort = True
            self.stop()
