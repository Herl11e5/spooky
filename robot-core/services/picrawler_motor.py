"""
services/picrawler_motor.py — MotorService for a robot driven by the picrawler body.

Same API as services/motor.py, so skills, mind, night watch and the dashboard run
unchanged; underneath, every motion is an intent to the body (PicrawlerClient)
instead of a SunFounder do_action() on the servos. The body (picrawler.service)
walks, enforces safety and arbitrates authority; this class only asks.

Mapping:
  forward/backward/turn_left/turn_right(speed)
      one step: a timed intent of step_s plus an overlap, so successive calls keep
      walking without stopping, and the call waits step_s as do_action() did —
      skills keep their pacing.
  move_forward/move_backward(distance cm, speed)
      timed from the body's speed limit.
  turn(degrees)          positive = right, spooky's convention (as for pan). Closed
                         on the body's camera compass; timed when it has none.
  stand / sit            enable / disable on the body (sit = sit down, servos off).
  look_at, look_center, tilt_head, pan_head
      the PiCrawler has no head, so a head look becomes a body lean in proportion:
      spooky's pan range maps onto the body's ±18° of yaw, its tilt range onto
      ±10° of pitch. Choreographies written for a quick head stay quick and
      expressive, take no steps and leave the heading alone — turning the body
      for every glance made it creep and wind round (158° in 40 s).
  nudge_toward           face tracking: leans after the face, and turns only once
                         the lean runs out — a face is worth facing.
  wave, shake_head, nod, wiggle_dance, spin_head, pat_desk, hop, stretch, flinch
      the body's gestures (body poses, no steps); hop becomes a bounce.
  climb, nudge           short walks.
  scan_environment       turns in steps on the compass, reading the distance each time.

Speeds: spooky's 0–100 become the body's 0–1 fraction, after SafetyMonitor.clamp_speed.
The body has the last word (obstacles, deadman, a person at its dashboard): a
refused intent is logged, not retried.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

from core.bus import EventBus, EventType
from core.safety import SafetyMonitor
from services.motor import Posture
from services.picrawler_client import PicrawlerClient

log = logging.getLogger(__name__)

_OVERLAP_S = 0.3          # an intent outlives its step by this much: no stop between steps
_TURN_TOLERANCE_DEG = 3.0
_MIN_TURN_RATE = 0.15     # rad/s — below this the gait barely turns
_LEAN_YAW_DEG = 18.0      # how far the body can lean left/right (the body's look limit)
_LEAN_PITCH_DEG = 10.0    # and up/down


def _scale(value: float, low: float, high: float, reach: float) -> float:
    """Map [low, 0, high] onto [-reach, 0, +reach], each side on its own."""
    if value >= 0:
        return value / high * reach if high > 0 else 0.0
    return value / -low * reach if low < 0 else 0.0


def _wrap_deg(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


class PicrawlerMotorService:

    def __init__(self, safety: SafetyMonitor, bus: EventBus, client: PicrawlerClient,
                 sensor=None, step_s: float = 0.8,
                 max_linear_mps: float = 0.10, max_angular_rps: float = 0.8) -> None:
        self._safety = safety
        self._bus = bus
        self._body = client
        self._sensor = sensor
        self._step_s = step_s
        self._max_lin = max_linear_mps
        self._max_ang = max_angular_rps
        self._posture = Posture.STAND
        self._pan_deg = 0.0      # where the robot "looks", relative to where it started
        self._tilt_deg = 0
        self._lean_yaw = 0.0     # the body's current lean, the body's convention (left > 0)
        self._lean_pitch = 0.0
        self._moving = False

        bus.subscribe(EventType.OBSTACLE_DETECTED, self._on_obstacle)
        bus.subscribe(EventType.SHUTDOWN_REQUEST, self._on_shutdown)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Stop moving. The body stays up; it sits down itself when its service stops."""
        log.info("PicrawlerMotorService: shutting down")
        self.stop()

    # ── steps (do_action equivalents) ─────────────────────────────────────────

    def forward(self, speed: int = 50) -> None:
        if self._safety.is_obstacle_blocked:
            log.debug("forward() suppressed — obstacle")
            return
        if not self._edge("forward"):
            self._step("MOVE_FORWARD", speed)

    def backward(self, speed: int = 40) -> None:
        if not self._edge("backward"):
            self._step("MOVE_BACKWARD", speed)

    def turn_left(self, speed: int = 40) -> None:
        if not self._edge("left"):
            self._step("TURN_LEFT", speed)

    def turn_right(self, speed: int = 40) -> None:
        if not self._edge("right"):
            self._step("TURN_RIGHT", speed)

    def stop(self) -> None:
        self._moving = False
        self._body.command("STOP")

    # ── distances and angles ──────────────────────────────────────────────────

    def move_forward(self, distance: float = 20, speed: int = 50) -> None:
        """Walk `distance` cm forward."""
        if self._safety.is_obstacle_blocked:
            log.debug("move_forward() suppressed — obstacle")
            return
        self._walk("MOVE_FORWARD", distance, speed)

    def move_backward(self, distance: float = 20, speed: int = 50) -> None:
        """Walk `distance` cm backward."""
        self._walk("MOVE_BACKWARD", distance, speed)

    def turn(self, degrees: float, speed: int = 40) -> None:
        """Turn by `degrees`: positive right, negative left."""
        if abs(degrees) < 1.0:
            return
        frac = self._frac(speed)
        if frac <= 0:
            return
        rate = max(_MIN_TURN_RATE, frac * self._max_ang)          # rad/s
        start = self._body.heading_deg()
        if start is None:
            # No compass on the body: timed, from the commanded rate.
            duration = math.radians(abs(degrees)) / rate
            self._body.command("TURN_RIGHT" if degrees > 0 else "TURN_LEFT", frac, duration)
            time.sleep(duration)
            return
        self._turn_to(start - degrees, rate, abs(degrees))        # heading is counter-clockwise

    def _turn_to(self, target: float, rate: float, span_deg: float) -> None:
        """Turn until the body's heading reads `target` (degrees, counter-clockwise)."""
        deadline = time.monotonic() + 2.0 * math.radians(span_deg) / rate + 1.0
        self._moving = True
        while time.monotonic() < deadline:
            heading = self._body.heading_deg()
            if heading is None:
                break
            error = _wrap_deg(target - heading)
            if abs(error) <= _TURN_TOLERANCE_DEG:
                break
            vyaw = max(-rate, min(rate, 2.0 * math.radians(error)))
            if abs(vyaw) < _MIN_TURN_RATE:
                vyaw = math.copysign(_MIN_TURN_RATE, vyaw)
            if not self._body.move(vyaw=vyaw):
                break                                              # refused: someone else drives
            time.sleep(0.1)
        self.stop()

    # ── posture ───────────────────────────────────────────────────────────────

    def stand(self) -> bool:
        """True when the body agreed (it refuses the brain unless allowed on the dashboard)."""
        self._posture = Posture.STAND
        return bool(self._body.enable())

    def sit(self) -> bool:
        self._posture = Posture.SIT
        return bool(self._body.disable())

    # ── looking: the body leans and turns, there is no head ──────────────────

    def look_at(self, pan: int = 0, tilt: int = 0) -> None:
        """
        pan > 0 = right, tilt > 0 = up, degrees of the head spooky imagines. The
        body leans in proportion — the whole pan range onto ±18°, the tilt range
        onto ±10° — without stepping.
        """
        pan = self._safety.clamp_pan(pan)
        tilt = self._safety.clamp_tilt(tilt)
        limits = self._safety._limits
        self._lean(_scale(pan, limits.pan_min, limits.pan_max, _LEAN_YAW_DEG),
                   _scale(tilt, limits.tilt_min, limits.tilt_max, _LEAN_PITCH_DEG))
        self._pan_deg, self._tilt_deg = pan, tilt

    def look_center(self) -> None:
        self.look_at(0, 0)

    def nudge_toward(self, dx: float, dy: float, gain: float = 0.05) -> None:
        """
        Bring a target at normalised (dx, dy) toward the centre of the image
        (dx +1 = right edge, dy +1 = bottom). Called on every face event: the body
        leans after it without stepping, and turns only once the lean runs out.
        """
        k = max(0.5, min(4.0, gain * 60))          # degrees of lean per unit of offset
        yaw = self._lean_yaw - dx * k              # body yaw: left is positive
        pitch = self._lean_pitch - dy * k
        if abs(yaw) > _LEAN_YAW_DEG:
            self._body.move(vyaw=math.copysign(0.25 * self._max_ang, yaw))   # out of lean: turn
            yaw = math.copysign(_LEAN_YAW_DEG, yaw)
        self._lean(-yaw, pitch)

    def pan_head(self, angle: Optional[float] = None, speed: Optional[int] = None) -> None:
        """pan_head(0): look ahead again. pan_head(speed=…): look around once."""
        if angle is not None:
            self.look_at(int(angle), self._tilt_deg)
            return
        self._gesture("look_around")

    def tilt_head(self, angle: float = 0) -> None:
        self.look_at(int(self._pan_deg), int(angle))

    # ── expressive moves: the body's gestures ─────────────────────────────────

    def wave(self) -> None:
        self._gesture("wave")

    def shake_head(self) -> None:
        self._gesture("shake")

    def nod(self) -> None:
        self._gesture("nod")

    def wiggle_dance(self) -> None:
        self._gesture("wiggle")

    def spin_head(self, speed: int = 80) -> None:
        self._gesture("look_around")

    def pat_desk(self) -> None:
        self._gesture("pat_desk")

    def hop(self) -> None:
        """A PiCrawler cannot hop: it bounces on its legs instead."""
        self._gesture("bounce")

    def stretch(self) -> None:
        self._gesture("stretch")

    def flinch(self) -> None:
        """Startled (skills/startle_skill.py): back and down, a moment frozen."""
        self._gesture("flinch")

    def climb(self) -> None:
        self.move_forward(distance=10, speed=40)

    def nudge(self) -> None:
        self.move_forward(distance=3, speed=30)

    # ── environment scan ──────────────────────────────────────────────────────

    def scan_environment(self, sensor_fn, n_steps: int = 12, speed: int = 30) -> list:
        """
        Turn a full circle in `n_steps`, reading `sensor_fn()` (cm) at each stop.
        Returns [{"angle": deg, "dist": cm}], 0° = the starting heading, clockwise.
        """
        step_deg = 360 / n_steps
        start = self._body.heading_deg()
        rate = max(_MIN_TURN_RATE, self._frac(speed) * self._max_ang)
        readings = []
        for i in range(n_steps):
            time.sleep(0.25)                      # let the reading settle after the turn
            dist = sensor_fn()
            readings.append({"angle": round(i * step_deg), "dist": round(dist, 1)})
            if start is None:
                self.turn(step_deg, speed)        # no compass: timed steps
            else:
                # Each stop is aimed from the starting heading, so the few degrees
                # every turn stops short do not add up round the circle.
                self._turn_to(start - (i + 1) * step_deg, rate, step_deg)
        return readings

    # ── state ─────────────────────────────────────────────────────────────────

    @property
    def is_moving(self) -> bool:
        return self._moving or self._body.is_moving()

    @property
    def posture(self) -> Posture:
        return self._posture

    @property
    def head_angles(self) -> tuple[int, int]:
        return (int(self._pan_deg), int(self._tilt_deg))

    @property
    def sim_mode(self) -> bool:
        return not self._body.connected

    # ── internals ─────────────────────────────────────────────────────────────

    def _frac(self, speed: int) -> float:
        return max(0.0, min(1.0, self._safety.clamp_speed(speed) / 100.0))

    def _step(self, action: str, speed: int) -> None:
        frac = self._frac(speed)
        if frac <= 0:
            return
        if self._body.command(action, frac, duration=self._step_s + _OVERLAP_S):
            self._moving = True
        time.sleep(self._step_s)

    def _walk(self, action: str, distance_cm: float, speed: int) -> None:
        frac = self._frac(speed)
        if frac <= 0 or distance_cm <= 0:
            return
        duration = min(5.0, (distance_cm / 100.0) / (frac * self._max_lin))
        if self._body.command(action, frac, duration=duration):
            self._moving = True
            time.sleep(duration)
            self._moving = False

    def _gesture(self, name: str) -> None:
        """Play a body gesture and wait for it, as do_action() did."""
        if self._body.gesture(name):
            self._lean_yaw = self._lean_pitch = 0.0     # a gesture ends in the stand pose
            self._moving = True
            time.sleep(self._body.gestures().get(name, 1.0))
            self._moving = False

    def _lean(self, pan_right_deg: float, tilt_up_deg: float) -> None:
        """Hold the body leaning `pan_right_deg` to the right and `tilt_up_deg` up."""
        yaw = max(-_LEAN_YAW_DEG, min(_LEAN_YAW_DEG, -pan_right_deg))
        if abs(yaw - self._lean_yaw) < 0.5 and abs(tilt_up_deg - self._lean_pitch) < 0.5:
            return
        if self._body.look(yaw=yaw, pitch=tilt_up_deg):
            self._lean_yaw, self._lean_pitch = yaw, tilt_up_deg

    def _edge(self, direction: str) -> bool:
        """Spooky's IMU edge check, kept for parity (the PiCrawler has no IMU: always False)."""
        if self._sensor is None or not self._sensor.is_edge(direction):
            return False
        log.warning(f"PicrawlerMotorService: edge detected '{direction}' — stopping")
        self.stop()
        self._bus.publish(EventType.OBSTACLE_DETECTED, {"type": "edge", "direction": direction},
                          source="MotorService")
        return True

    def _on_obstacle(self, ev) -> None:
        log.info(f"PicrawlerMotorService: obstacle at {ev.get('distance_cm', 0):.1f}cm — stop")
        self.stop()

    def _on_shutdown(self, ev) -> None:
        self.shutdown()

    def __repr__(self) -> str:
        return (f"<PicrawlerMotorService body={'up' if self._body.connected else 'unreachable'} "
                f"posture={self._posture.value} pan={self._pan_deg:.0f}° moving={self.is_moving}>")
