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
        self._cmd(lambda: self._crawler.do_action("forward", 1, speed))
        self._moving = True

    def backward(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        self._cmd(lambda: self._crawler.do_action("backward", 1, speed))
        self._moving = True

    def turn_left(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        self._cmd(lambda: self._crawler.do_action("turn left", 1, speed))

    def turn_right(self, speed: int = 40) -> None:
        speed = self._safety.clamp_speed(speed)
        self._cmd(lambda: self._crawler.do_action("turn right", 1, speed))

    def stop(self) -> None:
        self._moving = False
        self._cmd(lambda: self._crawler.do_action("stand", 1))

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
        self._cmd(lambda: self._crawler.set_cam_pan_angle(pan))
        self._cmd(lambda: self._crawler.set_cam_tilt_angle(tilt))

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
