"""
services/picrawler_client.py — Client of the picrawler body service.

Spooky is the brain; picrawler (the robot-emulator project, picrawler.service on
the Pi) is the body: the only process that drives the servos, owns the camera and
the ultrasonic sensor, and enforces safety — the robotd split of pollen-robotics'
microduck. Spooky never touches the hardware: it sends *intents* ("walk", "turn",
"stand") and reads the *state* the body publishes.

  - Standard library only (urllib + json): no new dependency for spooky.
  - Every command carries source="brain". The body ranks sources: a person at its
    dashboard preempts the brain, and a refused command just returns False.
  - The state is polled at 10 Hz on a background thread; getters return the last
    value and never block. Callers check `connected` / `state_age_s`.

API:
    body = PicrawlerClient("http://localhost:8000")
    body.start()
    body.command("MOVE_FORWARD", speed=0.5, duration=1.0)
    body.move(vx=0.0, vyaw=0.4)          # streamed: re-send within 0.5 s
    body.gesture("wave")                 # body poses, no steps; gestures() lists durations
    body.look(yaw=10, pitch=5)           # lean the body toward a direction (no head)
    body.distance_cm(), body.heading_deg(), body.camera_rgb()
    body.faces()                         # faces in view: position + identity embedding, no frames
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)


class PicrawlerClient:

    def __init__(self, base_url: str = "http://localhost:8000", source: str = "brain",
                 timeout_s: float = 2.0, poll_s: float = 0.1) -> None:
        self._url = base_url.rstrip("/")
        self._source = source
        self._timeout = timeout_s
        self._poll_s = poll_s
        self._state: dict[str, Any] = {}
        self._state_time = 0.0
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._warned = False
        self._gestures: dict[str, float] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(target=self._poll, daemon=True, name="Picrawler-state")
        self._thread.start()
        log.info(f"PicrawlerClient: body at {self._url} (source={self._source})")

    def stop(self) -> None:
        self._active = False

    def _poll(self) -> None:
        while self._active:
            state = self._request("GET", "/state")
            if isinstance(state, dict):
                self._state, self._state_time = state, time.monotonic()
                if self._warned:
                    log.info("PicrawlerClient: body reachable again")
                    self._warned = False
            elif not self._warned:
                log.warning(f"PicrawlerClient: body not reachable at {self._url}")
                self._warned = True
            time.sleep(self._poll_s)

    # ── state ─────────────────────────────────────────────────────────────────

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    @property
    def state_age_s(self) -> float:
        return time.monotonic() - self._state_time if self._state_time else float("inf")

    @property
    def connected(self) -> bool:
        return self.state_age_s < 1.0

    def distance_cm(self) -> float:
        """Forward distance in cm; 999 when the body has no fresh reading (spooky's convention)."""
        sensors = self._state.get("sensors", {})
        age = sensors.get("distance_age_s")
        if not self.connected or age is None or age > 0.5:
            return 999.0
        return float(sensors.get("distance_m", 9.99)) * 100.0

    def heading_deg(self) -> Optional[float]:
        """Measured heading (the body's camera compass), counter-clockwise; None if unknown."""
        if not self.connected:
            return None
        return self._state.get("heading_deg")

    def mode(self) -> str:
        return self._state.get("mode", "unknown") if self.connected else "unreachable"

    def is_moving(self) -> bool:
        return bool(self._state.get("is_moving")) if self.connected else False

    # ── commands (all source="brain") ─────────────────────────────────────────

    def command(self, action: str, speed: float = 0.5, duration: Optional[float] = None) -> bool:
        body = {"action": action, "speed": speed, "source": self._source}
        if duration is not None:
            body["duration"] = duration
        return self._ok(self._request("POST", "/command", body))

    def move(self, vx: float = 0.0, vy: float = 0.0, vyaw: float = 0.0) -> bool:
        """Streamed intent (m/s, rad/s). The body stops by itself unless re-sent within 0.5 s."""
        return self._ok(self._request("POST", "/move", {"vx": vx, "vy": vy, "vyaw": vyaw,
                                                         "source": self._source}))

    def gesture(self, name: str) -> bool:
        return self._ok(self._request("POST", "/gesture", {"name": name, "source": self._source}))

    def look(self, yaw: float = 0.0, pitch: float = 0.0) -> bool:
        """Body yaw (> 0 left) and pitch (> 0 up), degrees, held until look(0, 0)."""
        return self._ok(self._request("POST", "/look", {"yaw": yaw, "pitch": pitch,
                                                         "source": self._source}))

    def gestures(self) -> dict[str, float]:
        """Gesture name → duration in seconds, as the body plays them (cached)."""
        if not self._gestures:
            reply = self._request("GET", "/gestures")
            if isinstance(reply, dict):
                self._gestures = {k: float(v) for k, v in reply.items()}
        return self._gestures

    def enable(self) -> bool:
        return self._ok(self._request("POST", "/enable", {"source": self._source}))

    def disable(self) -> bool:
        return self._ok(self._request("POST", "/disable", {"source": self._source}))

    def emergency_stop(self) -> bool:
        return self._ok(self._request("POST", "/e_stop"))

    def faces(self) -> Optional[dict]:
        """
        The faces the body sees, largest first: {"status", "frame_size", "faces": [{id,
        box (fractions of the frame), bearing_deg (> 0 left), distance_m, embedding
        (128 floats, unit length, refreshed every ~0.5 s), ...}]}. None if unreachable.
        """
        reply = self._request("GET", "/faces")
        return reply if isinstance(reply, dict) else None

    def camera_jpeg(self) -> Optional[bytes]:
        try:
            with urllib.request.urlopen(self._url + "/camera", timeout=self._timeout) as r:
                return r.read() if r.status == 200 else None
        except (urllib.error.URLError, OSError):
            return None

    def camera_rgb(self):
        """The body's current camera frame as an RGB numpy array, or None."""
        data = self.camera_jpeg()
        if not data:
            return None
        import cv2
        import numpy as np
        bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    # ── internals ─────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read() or b"null")
            except ValueError:
                return None
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.debug(f"PicrawlerClient: {method} {path} failed — {e}")
            return None

    @staticmethod
    def _ok(reply) -> bool:
        if isinstance(reply, dict) and not reply.get("ok", False) and reply.get("authority"):
            log.debug(f"PicrawlerClient: refused, {reply['authority']} holds authority")
        return isinstance(reply, dict) and bool(reply.get("ok", False))
