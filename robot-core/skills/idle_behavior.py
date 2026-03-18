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
            # While person present just wait — skill stays alive
            if self._person_present:
                if not self.sleep(2.0):
                    break
                continue

            if not self._con.should_explore():
                if not self.sleep(8.0):
                    break
                continue

            behaviour = self._pick_behaviour()
            log.info(f"IdleBehaviorSkill: {behaviour}")
            self._execute(behaviour)

            rest = random.uniform(3.0, 8.0)
            if not self.sleep(rest):
                break

        self._motor.look_center()
        log.info("IdleBehaviorSkill: stopped")

    # ── behaviour selection ───────────────────────────────────────────────────

    def _pick_behaviour(self) -> str:
        curiosity = self._con.state.curiosity
        if curiosity > 0.8:
            choices = ["walk", "walk", "look_around", "curiosity_pan", "walk"]
        elif curiosity > 0.5:
            choices = ["walk", "look_around", "curiosity_pan", "walk", "yawn"]
        else:
            choices = ["walk", "curiosity_pan", "yawn", "look_around"]
        return random.choice(choices)

    def _execute(self, behaviour: str) -> None:
        if self.should_stop or self._person_present:
            return

        if behaviour == "look_around":
            self._choreo.play("look_around", wait=True)

        elif behaviour == "curiosity_pan":
            pan  = random.randint(-55, 55)
            tilt = random.randint(-10, 15)
            self._motor.look_at(pan=pan, tilt=tilt)
            self.sleep(random.uniform(1.5, 3.0))
            self._motor.look_center()

        elif behaviour == "walk":
            if self._safety.is_obstacle_blocked:
                # Can't go forward — turn instead
                self._motor.turn_left(speed=40)
                self.sleep(0.8)
                self._motor.stop()
                return
            steps = random.randint(2, 5)
            speed = random.randint(40, 55)
            for _ in range(steps):
                if self.should_stop or self._person_present or self._safety.is_obstacle_blocked:
                    break
                self._motor.forward(speed=speed)
                self.sleep(0.5)
            self._motor.stop()
            self.sleep(0.3)
            # Occasionally turn after walking
            if random.random() < 0.5:
                turn = self._motor.turn_left if random.random() < 0.5 else self._motor.turn_right
                turn(speed=38)
                self.sleep(random.uniform(0.4, 0.9))
                self._motor.stop()

        elif behaviour == "yawn":
            self._motor.look_at(pan=0, tilt=-20)
            self.sleep(1.2)
            self._motor.look_at(pan=0, tilt=0)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, ev) -> None:
        self._person_present = True   # _run() will pause; skill stays alive

    def _on_person_lost(self, ev) -> None:
        self._person_present = False  # _run() will resume exploration

    def _on_mode(self, ev) -> None:
        to = ev.get("to", "")
        if to not in {m.value for m in self.allowed_modes}:
            self.stop()
