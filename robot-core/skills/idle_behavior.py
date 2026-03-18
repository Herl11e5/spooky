"""
skills/idle_behavior.py — Ambient behaviours when no person is nearby.

Activated by MindService when Conscience.should_explore() is True
and no person has been seen for a while.

Behaviours (chosen randomly, weighted by curiosity level):
  - look_around:   scan the environment with the head
  - micro_move:    tiny forward/backward nudge then stop
  - curiosity_pan: look toward a random direction and hold
  - yawn:          slow tilt down, pause, tilt back up

Each behaviour lasts 3–8 seconds, then the skill re-evaluates.
The skill terminates itself when a person is detected or mode changes.
"""

from __future__ import annotations

import logging
import random
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


class IdleBehaviorSkill(Skill):
    """
    Runs low-key ambient behaviours when the robot is alone.
    Self-terminates on PERSON_DETECTED or mode change.
    """

    name          = "idle_behavior"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.IDLE_OBSERVER})

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
        self._person_present = False

        bus.subscribe(EventType.PERSON_DETECTED, self._on_person)
        bus.subscribe(EventType.PERSON_LOST,     self._on_person_lost)
        bus.subscribe(EventType.MODE_CHANGED,    self._on_mode)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("IdleBehaviorSkill: started")
        while not self.should_stop:
            if self._person_present:
                break
            if not self._con.should_explore():
                # Not curious enough right now — wait
                if not self.sleep(10.0):
                    break
                continue

            behaviour = self._pick_behaviour()
            log.debug(f"Idle behaviour: {behaviour}")
            self._execute(behaviour)

            # Rest between behaviours
            rest = random.uniform(4.0, 12.0)
            if not self.sleep(rest):
                break

        self._motor.look_center()
        log.info("IdleBehaviorSkill: stopped")

    # ── behaviour selection ───────────────────────────────────────────────────

    def _pick_behaviour(self) -> str:
        curiosity = self._con.state.curiosity
        # Higher curiosity → more active choices
        if curiosity > 0.8:
            choices = ["look_around", "look_around", "curiosity_pan", "micro_move"]
        elif curiosity > 0.6:
            choices = ["look_around", "curiosity_pan", "curiosity_pan", "yawn"]
        else:
            choices = ["curiosity_pan", "yawn", "yawn"]
        return random.choice(choices)

    def _execute(self, behaviour: str) -> None:
        if self.should_stop or self._person_present:
            return

        if behaviour == "look_around":
            self._choreo.play("look_around", wait=True)

        elif behaviour == "curiosity_pan":
            pan  = random.randint(-50, 50)
            tilt = random.randint(-10, 15)
            self._motor.look_at(pan=pan, tilt=tilt)
            self.sleep(random.uniform(1.5, 3.0))
            self._motor.look_center()

        elif behaviour == "micro_move":
            if self._safety.is_obstacle_blocked:
                return
            self._motor.forward(speed=25)
            self.sleep(0.4)
            self._motor.backward(speed=25)
            self.sleep(0.4)
            self._motor.stop()

        elif behaviour == "yawn":
            self._motor.look_at(pan=0, tilt=-20)
            self.sleep(1.2)
            self._motor.look_at(pan=0, tilt=0)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, ev) -> None:
        self._person_present = True
        self.stop()

    def _on_person_lost(self, ev) -> None:
        self._person_present = False

    def _on_mode(self, ev) -> None:
        to = ev.get("to", "")
        if to not in {m.value for m in self.allowed_modes}:
            self.stop()
