"""
services/motor.py — Motor and servo abstraction over PiCrawler.

Provides a clean, safety-checked interface. Hardware is imported lazily so
the module loads on any machine (dev/CI). Simulation mode is activated
automatically when picrawler is unavailable.

API:
    motor = MotorService(safety_monitor, bus)
    motor.forward(speed=40)
    motor.stop()
    motor.look_at(pan=15, tilt=-10)   # camera/head
    motor.stand()
    motor.sit()
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Optional

from core.bus import EventBus, EventType
from core.safety import SafetyMonitor

log = logging.getLogger(__name__)


# ── Hardware import (graceful) ────────────────────────────────────────────────

try:
    from picrawler import Picrawler
    from robot_hat import TTS, Music, Ultrasonic, Pin
    _HW_AVAILABLE = True
    log.info("MotorService: PiCrawler hardware detected")
except ImportError:
    _HW_AVAILABLE = False
    log.warning("MotorService: PiCrawler not available — simulation mode")


# ── Postures ──────────────────────────────────────────────────────────────────

class Posture(str, Enum):
    STAND   = "stand"
    SIT     = "sit"
    CROUCH  = "crouch"
    ALERT   = "alert"


# ── MotorService ──────────────────────────────────────────────────────────────

class MotorService:
    """
    Thread-safe motor/servo interface.

    All commands pass through SafetyMonitor.clamp_speed() before execution.
    If an obstacle is detected, forward motion is suppressed.
    Actuator exceptions increment the error counter.
    """

    # Head servo channels (PiCrawler defaults)
    _PAN_SERVO  = 0
    _TILT_SERVO = 1

    def __init__(self, safety: SafetyMonitor, bus: EventBus):
        self._safety   = safety
        self._bus      = bus
        self._lock     = threading.Lock()
        self._posture  = Posture.STAND
        self._pan_deg  = 0
        self._tilt_deg = 0
        self._moving   = False

        # Hardware handles
        self._crawler: Optional[object] = None
        self._pan_fn  = None   # resolved at init
        self._tilt_fn = None

        if _HW_AVAILABLE:
            self._init_hardware()
        else:
            log.info("MotorService: running in simulation mode")

        bus.subscribe(EventType.OBSTACLE_DETECTED, self._on_obstacle)
        bus.subscribe(EventType.SHUTDOWN_REQUEST,  self._on_shutdown)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _init_hardware(self) -> None:
        try:
            self._crawler = Picrawler()
            self._crawler.do_action("stand", 1)
            log.info("MotorService: PiCrawler initialised and standing")
        except Exception as e:
            log.error(f"MotorService: hardware init failed — {e}")
            self._safety.record_actuator_error(str(e))
            self._crawler = None
            return

        # Detect pan/tilt callable.
        # Priority: dedicated method > set_angle(servo_idx, angle)
        n_servos = len(self._crawler.servo_list) if hasattr(self._crawler, "servo_list") else 0
        # PiCrawler v2: 12 leg servos + servo 12=pan, 13=tilt
        pan_idx  = n_servos - 2 if n_servos >= 14 else 12
        tilt_idx = n_servos - 1 if n_servos >= 14 else 13

        self._pan_fn  = self._resolve_cam_fn(
            ["set_cam_pan_angle", "cam_pan_angle", "set_pan_angle", "set_cam_pan"],
            fallback_fn=getattr(self._crawler, "set_angle", None),
            fallback_idx=pan_idx,
        )
        self._tilt_fn = self._resolve_cam_fn(
            ["set_cam_tilt_angle", "cam_tilt_angle", "set_tilt_angle", "set_cam_tilt"],
            fallback_fn=getattr(self._crawler, "set_angle", None),
            fallback_idx=tilt_idx,
        )
        log.info(
            f"MotorService: servos={n_servos}  "
            f"pan_fn={getattr(self._pan_fn,'__name__','<lambda>')}  "
            f"tilt_fn={getattr(self._tilt_fn,'__name__','<lambda>')}"
        )

    def _resolve_cam_fn(self, names, fallback_fn, fallback_idx):
        """Return a callable(angle) for a camera servo axis."""
        for name in names:
            m = getattr(self._crawler, name, None)
            if callable(m):
                return m
        # Fall back to set_angle(idx, angle) if available
        if callable(fallback_fn):
            idx = fallback_idx
            return lambda angle, _fn=fallback_fn, _i=idx: _fn(_i, angle)
        log.warning("MotorService: no cam method found — head pan/tilt disabled")
        return None

    def shutdown(self) -> None:
        """Safe-stop: stop motion, sit down, release servos."""
        log.info("MotorService: shutting down")
        self._cmd(lambda: self._crawler.do_action("stand", 1))
        time.sleep(0.3)
        self.stop()

    # ── motion commands ───────────────────────────────────────────────────────

    def forward(self, speed: int = 50) -> None:
        if self._safety.is_obstacle_blocked:
            log.debug("forward() suppressed — obstacle")
            return
        speed = self._safety.clamp_speed(speed)
        log.debug(f"forward speed={speed}")
        self._cmd(lambda: self._crawler.do_action("forward", step=1, speed=speed))
        self._moving = True

    def backward(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        log.debug(f"backward speed={speed}")
        self._cmd(lambda: self._crawler.do_action("backward", step=1, speed=speed))
        self._moving = True

    def turn_left(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        self._cmd(lambda: self._crawler.do_action("turn left", step=1, speed=speed))

    def turn_right(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        self._cmd(lambda: self._crawler.do_action("turn right", step=1, speed=speed))

    def stop(self) -> None:
        self._moving = False
        self._cmd(lambda: self._crawler.do_action("stand", step=1))

    # ── posture ───────────────────────────────────────────────────────────────

    def stand(self) -> None:
        self._posture = Posture.STAND
        self._cmd(lambda: self._crawler.do_action("stand", 1))

    def sit(self) -> None:
        self._posture = Posture.SIT
        # PiCrawler doesn't have a "sit" action; use body-down approximation
        self._cmd(lambda: self._crawler.do_action("backward", 1, 20))
        time.sleep(0.2)
        self._cmd(lambda: self._crawler.do_action("stand", 1))

    def wave(self) -> None:
        self._cmd(lambda: self._crawler.do_action("wave", 2))

    def shake_head(self) -> None:
        self._cmd(lambda: self._crawler.do_action("shake head", 2))

    # ── head / camera pan-tilt ────────────────────────────────────────────────

    def look_at(self, pan: int = 0, tilt: int = 0) -> None:
        """
        Move head to (pan, tilt) degrees. Values are clamped by SafetyMonitor.
        pan  > 0 = right, < 0 = left
        tilt > 0 = up,    < 0 = down
        """
        pan  = self._safety.clamp_pan(pan)
        tilt = self._safety.clamp_tilt(tilt)
        self._pan_deg  = pan
        self._tilt_deg = tilt
        if self._pan_fn:
            self._soft_cmd(lambda: self._pan_fn(pan))
        if self._tilt_fn:
            self._soft_cmd(lambda: self._tilt_fn(tilt))

    def look_center(self) -> None:
        self.look_at(0, 0)

    def nudge_toward(self, dx: float, dy: float, gain: float = 0.05) -> None:
        """
        Nudge head to reduce (dx, dy) error.
        dx, dy are normalised offsets in [-1, 1].
        Called by face-tracking skill on each frame.
        """
        new_pan  = self._pan_deg  - int(dx * gain * 100)
        new_tilt = self._tilt_deg + int(dy * gain * 100)
        self.look_at(new_pan, new_tilt)

    # ── state ─────────────────────────────────────────────────────────────────

    @property
    def is_moving(self) -> bool:
        return self._moving

    @property
    def posture(self) -> Posture:
        return self._posture

    @property
    def head_angles(self) -> tuple[int, int]:
        return (self._pan_deg, self._tilt_deg)

    @property
    def sim_mode(self) -> bool:
        return self._crawler is None

    # ── internals ─────────────────────────────────────────────────────────────

    def _cmd(self, fn) -> None:
        """Execute a hardware command, logging and counting errors."""
        if self._crawler is None:
            log.debug(f"SIM motor: {fn}")
            return
        with self._lock:
            try:
                fn()
            except Exception as e:
                log.error(f"MotorService actuator error: {e}")
                self._safety.record_actuator_error(str(e))

    def _soft_cmd(self, fn) -> None:
        """Execute a command that should NOT trigger the safety error counter (e.g. pan/tilt)."""
        if self._crawler is None:
            return
        with self._lock:
            try:
                fn()
            except Exception as e:
                log.debug(f"MotorService pan/tilt soft error (ignored): {e}")

    def _on_obstacle(self, ev) -> None:
        dist = ev.get("distance_cm", 0)
        log.info(f"MotorService: obstacle at {dist:.1f}cm — emergency stop")
        self.stop()

    def _on_shutdown(self, ev) -> None:
        self.shutdown()

    def __repr__(self) -> str:
        hw = "sim" if self.sim_mode else "hw"
        return (
            f"<MotorService [{hw}] posture={self._posture.value} "
            f"head=({self._pan_deg}°,{self._tilt_deg}°) moving={self._moving}>"
        )
