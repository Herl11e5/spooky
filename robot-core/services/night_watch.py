"""
services/night_watch.py — Night Watch behavioral loop.

This service is dormant in day modes and activates when mode = NIGHT_WATCH.

Behavior per alert level (from AlertService fusion engine):
  L0 → already logged by AlertService; no body response
  L1 → orient camera toward event area, play "alert" animation, brief scan
  L2 → L1 + optional quiet TTS, save snapshot metadata to memory
  L3 → L2 + fire external adapters (done by AlertService), persistent log entry

Additional night-watch duties:
  - Enter: play sleep_pose, say goodnight (if not silent), start patrol thread
  - Exit:  stop patrol, generate nightly summary, say good morning
  - Periodic patrol: every N minutes, rotate 360° and scan
  - Snapshot: on L2+, save JPEG to data/snapshots/ (if enabled)

Privacy:
  - No continuous video recording
  - Snapshots are event-triggered and stored locally only
  - Snapshot saving is configurable (default: metadata only, no JPEG)
  - All data stays on device unless an external adapter is configured
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from services.memory import MemoryService

log = logging.getLogger(__name__)


# ── NightLogEntry ─────────────────────────────────────────────────────────────

class NightLogEntry:
    """Richer than AlertRecord: includes image path and response taken."""

    def __init__(
        self,
        level: int,
        reason: str,
        payload: Dict[str, Any],
        response: str = "",
        snapshot_path: Optional[str] = None,
    ):
        self.ts            = time.time()
        self.level         = level
        self.reason        = reason
        self.payload       = payload
        self.response      = response
        self.snapshot_path = snapshot_path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts":            self.ts,
            "level":         self.level,
            "reason":        self.reason,
            "payload":       self.payload,
            "response":      self.response,
            "snapshot_path": self.snapshot_path,
        }


# ── NightWatchService ─────────────────────────────────────────────────────────

class NightWatchService:
    """
    Drives the robot's body during night_watch mode.
    Listens for ALERT_RAISED events and escalates to physical responses.
    """

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        motor,          # MotorService
        choreography,   # Choreography
        audio,          # AudioService
        memory: MemoryService,
        vision,         # VisionService (for snapshots)
        safety,         # SafetyMonitor
        cfg,
    ):
        self._bus     = bus
        self._mm      = mode_manager
        self._motor   = motor
        self._choreo  = choreography
        self._audio   = audio
        self._memory  = memory
        self._vision  = vision
        self._safety  = safety
        self._cfg     = cfg

        # Config
        self._silent        = cfg.get("night_watch.silent_alerts",    True)
        self._snapshot_on_l2= cfg.get("night_watch.snapshot_on_l2",  True)
        self._snapshot_jpeg = cfg.get("night_watch.save_jpeg",        False)
        self._patrol_interval = cfg.get("night_watch.patrol_interval_s", 600)  # 10 min
        self._snapshot_dir  = Path(cfg.get("night_watch.snapshot_dir", "data/snapshots"))

        # State
        self._active_nw      = False
        self._night_log:     List[NightLogEntry] = []
        self._patrol_thread: Optional[threading.Thread] = None
        self._patrol_stop    = threading.Event()
        self._enter_ts:      Optional[float] = None

        # Subscribe
        bus.subscribe(EventType.ALERT_RAISED,  self._on_alert)
        bus.subscribe(EventType.MODE_CHANGED,  self._on_mode_changed)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        log.info("NightWatchService ready")

    def stop(self) -> None:
        self._stop_patrol()
        log.info("NightWatchService stopped")

    # ── mode transitions ──────────────────────────────────────────────────────

    def _on_mode_changed(self, ev) -> None:
        to   = ev.get("to", "")
        frm  = ev.get("from", "")
        if to == Mode.NIGHT_WATCH.value:
            # Mark active immediately (in bus thread) so alerts aren't missed
            self._active_nw = True
            self._enter_ts  = time.time()
            self._night_log.clear()
            self._patrol_stop.clear()
            threading.Thread(target=self._enter_night_watch, daemon=True,
                             name="NW-Enter").start()
        elif frm == Mode.NIGHT_WATCH.value:
            # Mark inactive immediately so we stop processing alerts
            self._active_nw = False
            threading.Thread(target=self._exit_night_watch, daemon=True,
                             name="NW-Exit").start()

    def _enter_night_watch(self) -> None:
        log.info("🌙 Entering night watch mode")

        # Physical: sleep pose + look around
        self._motor.look_center()
        self._choreo.play("look_around", wait=True)
        self._choreo.play("sleep_pose")

        if not self._silent:
            self._audio.say("Modalità sorveglianza notturna attivata. Buonanotte.")
        else:
            log.info("NightWatch: silent mode — no TTS")

        # Log entry
        self._memory.add_episode(
            what="Avviata sorveglianza notturna",
            action="mode_enter",
            outcome="success",
            mode=Mode.NIGHT_WATCH.value,
        )

        # Start periodic patrol
        self._patrol_thread = threading.Thread(
            target=self._patrol_loop, daemon=True, name="NW-Patrol"
        )
        self._patrol_thread.start()

    def _exit_night_watch(self) -> None:
        self._stop_patrol()

        log.info("🌅 Exiting night watch mode")

        # Physical: wake up
        self._choreo.play("wake_up", wait=True)
        self._motor.look_center()

        # Generate and store nightly summary
        summary = self._generate_summary()
        self._memory.upsert_fact("last_night_summary", summary, confidence=1.0)
        self._memory.add_episode(
            what=f"Fine sorveglianza notturna: {summary}",
            action="mode_exit",
            outcome="success",
            mode=Mode.NIGHT_WATCH.value,
        )

        if not self._silent:
            self._audio.say(f"Buongiorno! {summary}")
        else:
            log.info(f"NightWatch summary: {summary}")

    # ── alert response ────────────────────────────────────────────────────────

    def _on_alert(self, ev) -> None:
        if not self._active_nw:
            return
        level  = int(ev.get("level",  0))
        reason = ev.get("reason", "unknown")
        payload = dict(ev.payload)

        if level >= 1:
            threading.Thread(
                target=self._respond, args=(level, reason, payload),
                daemon=True, name=f"NW-L{level}"
            ).start()

    def _respond(self, level: int, reason: str, payload: Dict[str, Any]) -> None:
        # ── Record log entry immediately (before any physical delay) ──────────
        response_desc = f"L{level}: alert+scan" + ("" if self._silent else "+tts")
        entry = NightLogEntry(level=level, reason=reason, payload=payload,
                              response=response_desc)
        self._night_log.append(entry)

        self._memory.add_episode(
            what=f"Alert L{level}: {reason}",
            action=f"night_watch_l{level}",
            outcome="logged",
            confidence=payload.get("confidence", 0.8),
            mode=Mode.NIGHT_WATCH.value,
        )
        log.info(f"NightWatch L{level}: {reason}")

        # ── L1+: orient + scan ────────────────────────────────────────────────
        if self._choreo:
            self._choreo.play("alert", wait=True)
            self._choreo.play("look_around", wait=True)

        # ── L2+: snapshot + optional TTS ─────────────────────────────────────
        if level >= 2:
            if self._snapshot_on_l2:
                entry.snapshot_path = self._take_snapshot(level, reason)
            if not self._silent and self._audio:
                self._audio.say(self._build_alert_message(level, reason, payload))
            else:
                log.warning(f"NightWatch L{level}: {reason} (silent mode)")

        log.info(
            f"NightWatch response complete L{level} "
            f"(snapshot={'yes' if entry.snapshot_path else 'no'})"
        )

    # ── snapshot ──────────────────────────────────────────────────────────────

    def _take_snapshot(self, level: int, reason: str) -> Optional[str]:
        """
        Save a snapshot. By default: only metadata JSON (no JPEG).
        Set night_watch.save_jpeg: true in config for actual image files.
        """
        day_dir = self._snapshot_dir / datetime.now().strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        ts_str = datetime.now().strftime("%H-%M-%S")
        stem   = f"{ts_str}_L{level}_{reason.replace(' ', '_')}"

        # Always write metadata
        meta_path = day_dir / f"{stem}.json"
        meta = {
            "ts":       time.time(),
            "ts_human": datetime.now().isoformat(),
            "level":    level,
            "reason":   reason,
        }
        try:
            with open(meta_path, "w") as f:
                json.dump(meta, f, indent=2)
        except Exception as e:
            log.error(f"NightWatch: metadata write failed — {e}")
            return None

        # Optionally save JPEG
        if self._snapshot_jpeg and self._vision is not None:
            try:
                frame = self._vision.get_frame()
                if frame is not None:
                    import cv2
                    import numpy as np
                    jpg_path = day_dir / f"{stem}.jpg"
                    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    cv2.imwrite(str(jpg_path), bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    meta["snapshot_jpeg"] = str(jpg_path)
                    log.info(f"NightWatch: snapshot saved → {jpg_path}")
                    return str(jpg_path)
            except Exception as e:
                log.error(f"NightWatch: JPEG save failed — {e}")

        return str(meta_path)

    # ── periodic patrol ───────────────────────────────────────────────────────

    def _patrol_loop(self) -> None:
        log.info(f"NightWatch patrol: every {self._patrol_interval}s")
        while not self._patrol_stop.wait(timeout=self._patrol_interval):
            if not self._active_nw:
                break
            log.info("NightWatch: starting scheduled patrol scan")
            self._do_patrol_scan()
        log.info("NightWatch patrol: stopped")

    def _do_patrol_scan(self) -> None:
        """
        360° head scan to check for activity.
        Keeps the robot body stationary (safe for desk environment).
        """
        if self._safety.is_obstacle_blocked:
            log.info("NightWatch patrol: obstacle present — skip scan")
            return

        scan_positions = [
            (-55,  0), (-30,  5), (0,   5), (30,  5),
            ( 55,  0), ( 30, -5), (0,  -5), (-30, -5),
            (  0,  0),
        ]
        for pan, tilt in scan_positions:
            if self._patrol_stop.is_set() or not self._active_nw:
                break
            self._motor.look_at(pan=pan, tilt=tilt)
            self._patrol_stop.wait(timeout=0.8)

        self._motor.look_center()
        log.info("NightWatch patrol scan complete")

    def _stop_patrol(self) -> None:
        self._patrol_stop.set()
        if self._patrol_thread and self._patrol_thread.is_alive():
            self._patrol_thread.join(timeout=3.0)

    # ── nightly summary ───────────────────────────────────────────────────────

    def _generate_summary(self) -> str:
        duration_h = (time.time() - (self._enter_ts or time.time())) / 3600
        n = len(self._night_log)

        if n == 0:
            return f"Notte tranquilla ({duration_h:.1f}h di sorveglianza, nessun evento)."

        by_level: Dict[int, int] = {}
        for entry in self._night_log:
            by_level[entry.level] = by_level.get(entry.level, 0) + 1

        parts = []
        if by_level.get(3, 0):
            parts.append(f"{by_level[3]} alert critici")
        if by_level.get(2, 0):
            parts.append(f"{by_level[2]} alert medi")
        if by_level.get(1, 0):
            parts.append(f"{by_level[1]} eventi minori")

        summary = (
            f"Sorveglianza notturna completata ({duration_h:.1f}h). "
            f"Rilevati: {', '.join(parts)}."
        )

        # Latest significant event
        significant = [e for e in self._night_log if e.level >= 2]
        if significant:
            last = significant[-1]
            summary += f" Ultimo evento: {last.reason.replace('_', ' ')} alle {datetime.fromtimestamp(last.ts).strftime('%H:%M')}."

        return summary

    # ── inspection ────────────────────────────────────────────────────────────

    def night_log(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._night_log]

    def is_active(self) -> bool:
        return self._active_nw

    def __repr__(self) -> str:
        return (
            f"<NightWatchService active={self._active_nw} "
            f"events={len(self._night_log)} "
            f"silent={self._silent}>"
        )
