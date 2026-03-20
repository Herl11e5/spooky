"""
services/sensor.py — Sensor abstraction: ultrasonic, CPU temp, RAM.

Reads hardware periodically and publishes structured events.
Also exposes synchronous getters so SafetyMonitor can poll.

Simulation values are used when hardware is unavailable.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

from core.bus import EventBus, EventType

import math
import subprocess

log = logging.getLogger(__name__)


# ── EdgeDetector (MPU6050 via I2C) ────────────────────────────────────────────

class EdgeDetector:
    """
    Detects table/shelf edges using the MPU6050 accelerometer on the robot_hat
    (I2C bus 1, address 0x68).

    When a leg steps off an edge the robot body tilts in that direction.
    is_edge(direction) returns True only when tilt exceeds the threshold
    for the given movement direction — so forward motion is blocked only by
    forward tilt, backward only by backward tilt, etc.

    Pitch positive = nose up.   Roll positive = left side up.
    """

    _ADDR       = 0x68
    _PWR_MGMT_1 = 0x6B
    _ACCEL_BASE = 0x3B   # ACCEL_XOUT_H (then Y+2, Z+4)

    def __init__(self,
                 pitch_threshold: float = 10.0,
                 roll_threshold:  float = 10.0):
        self._pitch_thr = pitch_threshold
        self._roll_thr  = roll_threshold
        self._bus = None
        self._ok  = False
        try:
            import smbus2
            b = smbus2.SMBus(1)
            b.write_byte_data(self._ADDR, self._PWR_MGMT_1, 0)   # wake
            self._bus = b
            self._ok  = True
            log.info("EdgeDetector: MPU6050 on I2C-1 ok")
        except Exception as e:
            log.warning(f"EdgeDetector: MPU6050 not available ({e}) — edge detection disabled")

    # ── public ────────────────────────────────────────────────────────────────

    def get_pitch_roll(self) -> tuple[float, float]:
        """Return (pitch_deg, roll_deg). (0, 0) if hardware not available."""
        if not self._ok:
            return 0.0, 0.0
        try:
            ax, ay, az = self._read_accel()
            pitch = math.degrees(math.atan2(-ax, math.sqrt(ay**2 + az**2)))
            roll  = math.degrees(math.atan2(ay, az))
            return pitch, roll
        except Exception as e:
            log.debug(f"EdgeDetector read error: {e}")
            return 0.0, 0.0

    def is_edge(self, direction: str) -> bool:
        """
        Return True if the robot is tilting in a way that suggests a surface
        edge in 'direction'.  direction ∈ {"forward","backward","left","right"}.
        Only the axis relevant to the direction is checked.
        """
        if not self._ok:
            return False
        pitch, roll = self.get_pitch_roll()
        if direction == "forward"  and pitch < -self._pitch_thr:
            log.info(f"EdgeDetector: BORDO avanti rilevato (pitch={pitch:.1f}°)")
            return True
        if direction == "backward" and pitch >  self._pitch_thr:
            log.info(f"EdgeDetector: BORDO indietro rilevato (pitch={pitch:.1f}°)")
            return True
        if direction == "left"     and roll  < -self._roll_thr:
            log.info(f"EdgeDetector: BORDO sinistra rilevato (roll={roll:.1f}°)")
            return True
        if direction == "right"    and roll  >  self._roll_thr:
            log.info(f"EdgeDetector: BORDO destra rilevato (roll={roll:.1f}°)")
            return True
        return False

    # ── private ───────────────────────────────────────────────────────────────

    def _read_accel(self) -> tuple[float, float, float]:
        """Read ax, ay, az in g (±2g range, 16384 LSB/g)."""
        raw = self._bus.read_i2c_block_data(self._ADDR, self._ACCEL_BASE, 6)
        def s16(hi, lo):
            v = (hi << 8) | lo
            return v - 65536 if v > 32767 else v
        ax = s16(raw[0], raw[1]) / 16384.0
        ay = s16(raw[2], raw[3]) / 16384.0
        az = s16(raw[4], raw[5]) / 16384.0
        return ax, ay, az

    @property
    def available(self) -> bool:
        return self._ok


# ── Hardware import ───────────────────────────────────────────────────────────

try:
    from robot_hat import Ultrasonic, Pin
    _ULTRASONIC_AVAILABLE = True
except ImportError:
    _ULTRASONIC_AVAILABLE = False


# ── SensorService ─────────────────────────────────────────────────────────────

class SensorService:
    """
    Polls ultrasonic sensor, CPU temperature, and RAM.
    Publishes events on notable changes.
    Exposes simple getter methods for SafetyMonitor callbacks.

    Run interval: ~100 ms for ultrasonic, ~2 s for system metrics.
    """

    # PiCrawler ultrasonic wiring (robot_hat)
    _TRIG_PIN = "D2"
    _ECHO_PIN = "D3"

    def __init__(
        self,
        bus: EventBus,
        poll_interval_s: float = 0.10,
        system_interval_s: float = 2.0,
    ):
        self._bus       = bus
        self._poll_s    = poll_interval_s
        self._sys_s     = system_interval_s
        self._active    = False
        self._thread:   Optional[threading.Thread] = None
        self._sys_thread: Optional[threading.Thread] = None

        # Latest readings (thread-safe via GIL for simple float/int)
        self._distance_cm: float = 999.0
        self._cpu_temp_c:  float = 0.0
        self._ram_free_mb: int   = 8192

        # Edge detector (MPU6050)
        self._edge = EdgeDetector()
        self._pitch: float = 0.0
        self._roll:  float = 0.0

        # Hardware
        self._ultrasonic: Optional[object] = None
        if _ULTRASONIC_AVAILABLE:
            try:
                self._ultrasonic = Ultrasonic(Pin(self._TRIG_PIN), Pin(self._ECHO_PIN))
                log.info("SensorService: ultrasonic initialised")
            except Exception as e:
                log.warning(f"SensorService: ultrasonic init failed — {e}")

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._run_ultrasonic, daemon=True, name="Sensor-US"
        )
        self._sys_thread = threading.Thread(
            target=self._run_system, daemon=True, name="Sensor-Sys"
        )
        self._thread.start()
        self._sys_thread.start()
        log.info("SensorService started")

    def stop(self) -> None:
        self._active = False
        log.info("SensorService stopped")

    # ── synchronous getters (for SafetyMonitor callbacks) ────────────────────

    def get_distance_cm(self) -> float:
        return self._distance_cm

    def get_cpu_temp_c(self) -> float:
        return self._cpu_temp_c

    def get_ram_free_mb(self) -> int:
        return self._ram_free_mb

    def get_pitch_roll(self) -> tuple[float, float]:
        return self._pitch, self._roll

    def is_edge(self, direction: str) -> bool:
        return self._edge.is_edge(direction)

    @property
    def edge_detector(self) -> EdgeDetector:
        return self._edge

    # ── poll loops ────────────────────────────────────────────────────────────

    def _run_ultrasonic(self) -> None:
        while self._active:
            try:
                dist = self._read_distance()
                self._distance_cm = dist
            except Exception as e:
                log.debug(f"SensorService ultrasonic read error: {e}")
            time.sleep(self._poll_s)

    def _run_system(self) -> None:
        while self._active:
            try:
                temp  = self._read_cpu_temp()
                ram   = self._read_ram_free_mb()
                pitch, roll = self._edge.get_pitch_roll()
                self._cpu_temp_c  = temp
                self._ram_free_mb = ram
                self._pitch = pitch
                self._roll  = roll
                # Publish heartbeat with current system state
                self._bus.publish(
                    EventType.HEARTBEAT,
                    {
                        "distance_cm": round(self._distance_cm, 1),
                        "cpu_temp_c":  round(temp, 1),
                        "ram_free_mb": ram,
                        "pitch_deg":   round(pitch, 1),
                        "roll_deg":    round(roll,  1),
                    },
                    source="SensorService",
                )
            except Exception as e:
                log.error(f"SensorService system read error: {e}")
            time.sleep(self._sys_s)

    # ── hardware readers ──────────────────────────────────────────────────────

    def _read_distance(self) -> float:
        if self._ultrasonic is not None:
            val = self._ultrasonic.read()
            if val is not None and val > 0:
                return float(val)
        return 999.0   # simulation / no reading

    def _read_cpu_temp(self) -> float:
        # 1. vcgencmd (RPi nativo — più affidabile)
        try:
            import subprocess
            out = subprocess.check_output(
                ["vcgencmd", "measure_temp"], timeout=2
            ).decode().strip()          # "temp=48.5'C"
            return float(out.split("=")[1].rstrip("'C"))
        except Exception:
            pass
        # 2. Thermal zone sysfs (Linux generico)
        import glob
        for path in sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp")):
            try:
                val = int(Path(path).read_text().strip())
                if val > 0:
                    return val / 1000.0
            except Exception:
                continue
        # 3. psutil
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            for key in ("cpu_thermal", "cpu-thermal", "soc_thermal", "coretemp"):
                if key in temps and temps[key]:
                    return temps[key][0].current
        except Exception:
            pass
        return 0.0

    def _read_ram_free_mb(self) -> int:
        # 1. /proc/meminfo (Linux — più affidabile)
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        val = int(line.split()[1]) // 1024
                        if val > 0:
                            return val
        except Exception:
            pass
        # 2. free -m (shell)
        try:
            import subprocess
            out = subprocess.check_output(["free", "-m"], timeout=2).decode()
            for line in out.splitlines():
                if line.startswith("Mem:"):
                    parts = line.split()
                    # free -m: Mem: total used free shared buff/cache available
                    if len(parts) >= 7:
                        return int(parts[6])   # "available" column
            # fallback: total - used
            for line in out.splitlines():
                if line.startswith("Mem:"):
                    parts = line.split()
                    if len(parts) >= 3:
                        return max(0, int(parts[1]) - int(parts[2]))
        except Exception:
            pass
        # 3. psutil
        try:
            import psutil
            return psutil.virtual_memory().available // (1024 * 1024)
        except Exception:
            pass
        return 1024   # assume disponibile se non riusciamo a leggere

    def __repr__(self) -> str:
        return (
            f"<SensorService dist={self._distance_cm:.1f}cm "
            f"temp={self._cpu_temp_c:.1f}°C ram={self._ram_free_mb}MB>"
        )
