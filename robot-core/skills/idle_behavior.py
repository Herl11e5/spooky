"""
skills/idle_behavior.py — Ambient behaviours when no person is nearby.

Like Vector, Spooky is NEVER completely still. This skill runs in three
tiers based on how long the robot has been alone:

  Tier 1 (always):  micro-movements — fidgets, head tilts, glances.
                    These happen every few seconds regardless of curiosity.

  Tier 2 (curious): active exploration — walk, look_around, peek, sniff.
                    Requires conscience.should_explore() (curiosity > 0.65).

  Tier 3 (bored):   escalating attention-seeking — dramatic sighs, longer
                    wanders, peek around objects. Activates after 3+ min alone.

The skill also reacts immediately to:
  - UNUSUAL_SOUND   → startle + investigate direction
  - MOTION_DETECTED → snap attention toward motion
  - PICKED_UP       → react with "picked_up_react" choreography
  - PUT_DOWN        → orient after landing
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
    Runs continuous ambient behaviours. Always alive; never fully stops
    unless mode changes to an incompatible state.
    """

    name          = "idle_behavior"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.IDLE_OBSERVER})

    # How long alone before escalating to boredom tier
    _BORED_AFTER_S = 180.0   # 3 minutes

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
        self._person_present   = False
        self._alone_since: float = time.time()
        self._last_sound_time: float = 0.0
        self._last_motion_time: float = 0.0
        self._startle_pending  = False
        self._picked_up        = False

        bus.subscribe(EventType.PERSON_DETECTED,  self._on_person)
        bus.subscribe(EventType.PERSON_LOST,      self._on_person_lost)
        bus.subscribe(EventType.MODE_CHANGED,     self._on_mode)
        bus.subscribe(EventType.UNUSUAL_SOUND,    self._on_sound)
        bus.subscribe(EventType.MOTION_DETECTED,  self._on_motion)
        bus.subscribe(EventType.PICKED_UP,        self._on_picked_up)
        bus.subscribe(EventType.PUT_DOWN,         self._on_put_down)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("IdleBehaviorSkill: started")
        while not self.should_stop:

            # React immediately to being picked up
            if self._picked_up:
                self._picked_up = False
                self._choreo.play("picked_up_react", wait=True)
                if not self.sleep(1.0):
                    break
                continue

            # While person is present, just wait (skill stays alive)
            if self._person_present:
                if not self.sleep(1.5):
                    break
                continue

            # React to startle (sound/motion while alone)
            if self._startle_pending:
                self._startle_pending = False
                self._choreo.play("startle", wait=True)
                if not self.sleep(0.5):
                    break
                self._choreo.play("alert", wait=True)
                if not self.sleep(1.0):
                    break
                continue

            alone_for = time.time() - self._alone_since
            behaviour = self._pick_behaviour(alone_for)
            log.debug(f"IdleBehaviorSkill: {behaviour} (alone {alone_for:.0f}s)")
            self._execute(behaviour)

            # Rest between behaviours — shorter when curious, longer when tired
            curiosity = self._con.state.curiosity
            rest = random.uniform(1.5, 4.0) if curiosity > 0.5 else random.uniform(3.0, 7.0)
            if not self.sleep(rest):
                break

        self._motor.look_center()
        log.info("IdleBehaviorSkill: stopped")

    # ── behaviour selection ───────────────────────────────────────────────────

    def _pick_behaviour(self, alone_for: float) -> str:
        curiosity = self._con.state.curiosity

        # Tier 3 — bored, escalating
        if alone_for > self._BORED_AFTER_S:
            return random.choice([
                "bored_sigh", "bored_sigh",
                "walk", "peek_left", "peek_right",
                "look_around", "double_take",
                "walk",
            ])

        # Tier 2 — active exploration
        if curiosity > 0.65:
            return random.choice([
                "walk", "walk",
                "look_around", "investigate",
                "curiosity_pan", "sniff",
                "peek_left", "peek_right",
                "double_take",
            ])

        # Tier 1 — always-on micro-movements (even at low curiosity)
        return random.choice([
            "fidget", "fidget",
            "head_tilt_left", "head_tilt_right",
            "curiosity_pan",
            "yawn",
        ])

    # ── behaviour execution ───────────────────────────────────────────────────

    def _execute(self, behaviour: str) -> None:
        if self.should_stop or self._person_present or self._picked_up:
            return

        # Choreography-backed behaviours
        choreo_map = {
            "look_around":    "look_around",
            "investigate":    "investigate",
            "fidget":         "fidget",
            "head_tilt_left": "head_tilt_left",
            "head_tilt_right":"head_tilt_right",
            "sniff":          "sniff",
            "peek_left":      "peek_left",
            "peek_right":     "peek_right",
            "double_take":    "double_take",
            "bored_sigh":     "bored_sigh",
            "yawn":           "sleep_pose",   # repurpose existing
        }
        if behaviour in choreo_map:
            self._choreo.play(choreo_map[behaviour], wait=True)
            return

        if behaviour == "curiosity_pan":
            pan  = random.randint(-55, 55)
            tilt = random.randint(-10, 15)
            self._motor.look_at(pan=pan, tilt=tilt)
            self.sleep(random.uniform(1.2, 2.5))
            self._motor.look_center()
            return

        if behaviour == "walk":
            if self._safety.is_obstacle_blocked:
                self._motor.turn_left(speed=40)
                self.sleep(0.7)
                self._motor.stop()
                return
            steps = random.randint(2, 5)
            speed = random.randint(38, 55)
            for _ in range(steps):
                if self.should_stop or self._person_present or self._safety.is_obstacle_blocked:
                    break
                self._motor.forward(speed=speed)
                self.sleep(0.45)
            self._motor.stop()
            self.sleep(0.2)
            if random.random() < 0.5:
                turn_fn = self._motor.turn_left if random.random() < 0.5 else self._motor.turn_right
                turn_fn(speed=38)
                self.sleep(random.uniform(0.3, 0.8))
                self._motor.stop()
            return

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, ev) -> None:
        self._person_present = True

    def _on_person_lost(self, ev) -> None:
        self._person_present = False
        self._alone_since = time.time()

    def _on_mode(self, ev) -> None:
        to = ev.get("to", "")
        if to not in {m.value for m in self.allowed_modes}:
            self.stop()

    def _on_sound(self, ev) -> None:
        if not self._person_present:
            self._startle_pending = True
            self._last_sound_time = time.time()

    def _on_motion(self, ev) -> None:
        if not self._person_present:
            self._startle_pending = True
            self._last_motion_time = time.time()

    def _on_picked_up(self, ev) -> None:
        self._picked_up = True

    def _on_put_down(self, ev) -> None:
        self._choreo.play("put_down_react")
