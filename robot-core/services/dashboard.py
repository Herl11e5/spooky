"""
services/dashboard.py — Cartoon-style live dashboard.

GET  /            → HTML dashboard
GET  /camera      → MJPEG stream
GET  /api/state   → full JSON state
GET  /api/persons → known persons
GET  /api/memory  → episodes + facts
GET  /api/alerts  → night watch alerts
POST /api/command → inject command {"command": "..."}
POST /api/mode    → switch mode    {"mode": "..."}
POST /api/summarize
GET  /stream      → SSE live events
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


class SharedState:
    def __init__(self):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "mode": "companion_day", "mood": "content",
            "energy": 1.0, "social_drive": 0.5, "curiosity": 0.6,
            "interaction_fatigue": 0.0, "attention": 0.0,
            "scene_description": "", "detected_objects": "",
            "tts_speaking": False, "obstacle_blocked": False,
            "distance_cm": 999.0, "cpu_temp_c": 0.0, "ram_free_mb": 8192,
            "chat_history": [], "last_person": None,
            "mic_state": "idle",  # idle | listening | thinking
            "last_transcript": "",
            "face_detected": False, "face_confidence": 0.0,
        }

    def update(self, **kwargs):
        with self._lock:
            self._data.update(kwargs)

    def add_chat(self, role: str, text: str):
        with self._lock:
            self._data["chat_history"].append({"role": role, "text": text, "ts": time.time()})
            if len(self._data["chat_history"]) > 60:
                self._data["chat_history"] = self._data["chat_history"][-60:]

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def __getitem__(self, k):
        with self._lock:
            return self._data[k]


shared = SharedState()


class DashboardService:
    def __init__(self, bus, mode_manager, memory, alert_svc, conscience,
                 cmd_queue, cfg, night_watch=None, experiments=None,
                 summarizer=None, learning=None, vision=None, motor=None,
                 audio=None, mind=None, sensor=None):
        self._bus = bus
        self._mm = mode_manager
        self._memory = memory
        self._alerts = alert_svc
        self._conscience = conscience
        self._cmd_q = cmd_queue
        self._cfg = cfg
        self._nw = night_watch
        self._exp = experiments
        self._summ = summarizer
        self._learn = learning
        self._vision = vision
        self._motor = motor
        self._audio = audio
        self._mind = mind
        self._sensor = sensor
        self._last_scan: list = []
        self._host = cfg.get("dashboard.host", "0.0.0.0")
        self._port = int(cfg.get("dashboard.port", 5000))
        self._sse_clients: list[queue.Queue] = []
        self._sse_lock = threading.Lock()

        bus.subscribe(EventType.MODE_CHANGED,      self._on_mode)
        bus.subscribe(EventType.SCENE_ANALYZED,    self._on_scene)
        bus.subscribe(EventType.OBJECTS_DETECTED,  self._on_objects)
        bus.subscribe(EventType.TTS_STARTED,       self._on_tts_start)
        bus.subscribe(EventType.TTS_FINISHED,      self._on_tts_finish)
        bus.subscribe(EventType.OBSTACLE_DETECTED, self._on_obstacle)
        bus.subscribe(EventType.OBSTACLE_CLEARED,  self._on_obstacle_clear)
        bus.subscribe(EventType.HEARTBEAT,         self._on_heartbeat)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_id)
        bus.subscribe(EventType.PERSON_DETECTED,   self._on_person_det)
        bus.subscribe(EventType.PERSON_LOST,       self._on_person_lost)
        bus.subscribe(EventType.ALERT_RAISED,      self._on_alert)
        bus.subscribe(EventType.MIC_STATE_CHANGED, self._on_mic_state)
        bus.subscribe(EventType.WAKE_WORD_DETECTED,self._on_wake)
        bus.subscribe(EventType.SPEECH_TRANSCRIBED,self._on_transcript)
        bus.subscribe(EventType.COMMAND_PARSED,    self._on_command_parsed)
        bus.subscribe(EventType.LLM_CALL,          self._on_llm_call)
        bus.subscribe("*",                         self._on_any)

    def start(self):
        threading.Thread(target=self._run_flask, daemon=True, name="Dashboard").start()

    def _run_flask(self):
        try:
            from flask import Flask, request, jsonify, Response, render_template_string
        except ImportError:
            log.error("flask not installed"); return

        app = Flask(__name__)
        app.logger.setLevel(logging.ERROR)

        @app.route("/")
        def index():
            return render_template_string(_HTML)

        @app.route("/camera")
        def camera():
            if self._vision is None:
                return "no camera", 503
            return Response(self._mjpeg(), mimetype="multipart/x-mixed-replace; boundary=frame")

        @app.route("/api/state")
        def api_state():
            s = shared.snapshot()
            if self._conscience:
                s["drives"] = self._conscience.to_dict()
            s["mode"] = self._mm.current.value
            return jsonify(s)

        @app.route("/api/diag")
        def api_diag():
            import subprocess, shutil
            diag = {}
            # Ollama
            try:
                import ollama
                models = [m.model for m in ollama.list().models]
                diag["ollama"] = {"running": True, "models": models}
            except Exception as e:
                diag["ollama"] = {"running": False, "error": str(e)}
            # espeak-ng
            diag["espeak"] = shutil.which("espeak-ng") is not None
            # aplay devices
            try:
                out = subprocess.check_output(["aplay", "-l"], stderr=subprocess.STDOUT,
                                              timeout=3).decode(errors="replace")
                diag["aplay_devices"] = [l for l in out.splitlines() if l.startswith("card")]
            except Exception as e:
                diag["aplay_devices"] = [str(e)]
            # sounddevice mic devices
            try:
                import sounddevice as sd
                devs = sd.query_devices()
                diag["mic_devices"] = [
                    {"idx": i, "name": d["name"], "rate": int(d["default_samplerate"])}
                    for i, d in enumerate(devs) if d["max_input_channels"] > 0
                ]
                diag["mic_default"] = sd.default.device[0]
            except Exception as e:
                diag["mic_devices"] = []; diag["mic_default"] = str(e)
            # Audio method
            if self._audio:
                diag["tts_method"] = getattr(self._audio.output, "_method", "?")
                diag["stt_active"] = getattr(self._audio.input, "_active", False)
            # LLM model
            if self._mind:
                brain = getattr(self._mind, "_brain", None)
                diag["llm_model"] = getattr(brain, "_model", None)
                diag["llm_ok"] = getattr(brain, "_ok", False)
            return jsonify(diag)

        @app.route("/api/sensors")
        def api_sensors():
            if self._sensor:
                pitch, roll = self._sensor.get_pitch_roll()
                return jsonify({
                    "dist":  round(self._sensor.get_distance_cm(), 1),
                    "temp":  round(self._sensor.get_cpu_temp_c(), 1),
                    "ram":   self._sensor.get_ram_free_mb(),
                    "pitch": round(pitch, 1),
                    "roll":  round(roll,  1),
                    "edge_imu": self._sensor.edge_detector.available,
                })
            s = shared.snapshot()
            return jsonify({
                "dist": s.get("distance_cm", 999),
                "temp": s.get("cpu_temp_c", 0),
                "ram":  s.get("ram_free_mb", 8192),
            })

        @app.route("/api/vision")
        def api_vision():
            if self._vision:
                return jsonify({
                    "scene":   self._vision.last_scene,
                    "objects": self._vision.last_objects,
                    "model":   getattr(self._vision, "_vision_model", None),
                    "camera":  getattr(self._vision._camera, "backend", "?"),
                })
            return jsonify({"scene": "", "objects": "", "model": None, "camera": "none"})

        @app.route("/api/scan", methods=["POST"])
        def api_scan():
            if not self._motor or not self._sensor:
                return jsonify({"error": "motor/sensor not available"}), 503
            import threading as _th
            def _do_scan():
                readings = self._motor.scan_environment(
                    self._sensor.get_distance_cm, n_steps=12, speed=30
                )
                self._last_scan = readings
                self._broadcast({"type": "scan_complete", "readings": readings})
            _th.Thread(target=_do_scan, daemon=True, name="EnvScan").start()
            return jsonify({"status": "scanning"})

        @app.route("/api/scan", methods=["GET"])
        def api_scan_get():
            return jsonify({"readings": getattr(self, "_last_scan", [])})

        @app.route("/api/persons")
        def api_persons():
            return jsonify(self._memory.all_persons())

        @app.route("/api/memory")
        def api_memory():
            return jsonify({"episodes": self._memory.recent_episodes(20),
                            "facts": self._memory.all_facts()})

        @app.route("/api/alerts")
        def api_alerts():
            return jsonify([
                {"level": a.level, "reason": a.reason, "ts": a.ts, **a.payload}
                for a in self._alerts.recent_alerts(30)
            ])

        @app.route("/api/night_log")
        def api_night_log():
            return jsonify(self._nw.night_log() if self._nw else [])

        @app.route("/api/experiments")
        def api_experiments():
            if not self._exp:
                return jsonify({"current": None, "history": []})
            cur = self._exp.current.to_dict() if self._exp.current else None
            return jsonify({"current": cur, "history": self._exp.history()})

        @app.route("/api/summarize", methods=["POST"])
        def api_summarize():
            if not self._summ:
                return jsonify({"error": "no summarizer"}), 503
            threading.Thread(target=self._summ.summarize_now, daemon=True).start()
            return jsonify({"ok": True})

        @app.route("/api/learning")
        def api_learning():
            return jsonify(self._learn.session_summary() if self._learn else {})

        @app.route("/api/command", methods=["POST"])
        def api_command():
            data = request.get_json(silent=True) or {}
            cmd = (data.get("command") or "").strip()
            if not cmd:
                return jsonify({"error": "no command"}), 400
            self._cmd_q.put(cmd)
            shared.add_chat("user", cmd)
            return jsonify({"ok": True})

        @app.route("/api/mode", methods=["POST"])
        def api_mode():
            data = request.get_json(silent=True) or {}
            try:
                target = Mode((data.get("mode") or "").strip())
            except ValueError:
                return jsonify({"error": "unknown mode"}), 400
            ok = self._mm.request_transition(target, reason="dashboard")
            return jsonify({"ok": ok, "current": self._mm.current.value})

        @app.route("/api/motor", methods=["POST"])
        def api_motor():
            if not self._motor:
                return jsonify({"error": "no motor"}), 503
            data   = request.get_json(silent=True) or {}
            action = (data.get("action") or "").strip()
            speed  = int(data.get("speed", 50))
            dur    = float(data.get("duration", 0.8))
            _m = self._motor
            def _run():
                if action == "forward":
                    _m.forward(speed=speed); time.sleep(dur); _m.stop()
                elif action == "backward":
                    _m.backward(speed=speed); time.sleep(dur); _m.stop()
                elif action == "left":
                    _m.turn_left(speed=speed); time.sleep(dur); _m.stop()
                elif action == "right":
                    _m.turn_right(speed=speed); time.sleep(dur); _m.stop()
                elif action == "stop":
                    _m.stop()
                elif action == "wave":
                    _m.wave()
                elif action == "center":
                    _m.look_center()
            threading.Thread(target=_run, daemon=True).start()
            return jsonify({"ok": True, "action": action})

        @app.route("/stream")
        def stream():
            q: queue.Queue = queue.Queue(maxsize=200)
            with self._sse_lock:
                self._sse_clients.append(q)
            def gen():
                try:
                    while True:
                        try:
                            yield f"data: {q.get(timeout=20)}\n\n"
                        except queue.Empty:
                            yield 'data: {"type":"ping"}\n\n'
                except GeneratorExit:
                    pass
                finally:
                    with self._sse_lock:
                        if q in self._sse_clients:
                            self._sse_clients.remove(q)
            return Response(gen(), mimetype="text/event-stream",
                            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

        import socket as _s
        port = self._port
        for _ in range(5):
            with _s.socket(_s.AF_INET, _s.SOCK_STREAM) as s:
                s.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
                try:
                    s.bind(("0.0.0.0", port)); break
                except OSError:
                    log.warning(f"Dashboard: port {port} busy → {port+1}")
                    port += 1
        log.info(f"Dashboard: http://{self._host}:{port}")
        app.run(host=self._host, port=port, threaded=True, use_reloader=False)

    def _mjpeg(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            return

        def _placeholder() -> bytes:
            """400×300 dark frame with status text — sent when camera is not ready."""
            img = np.zeros((300, 400, 3), dtype=np.uint8)
            img[:] = (30, 30, 30)
            cv2.putText(img, "Camera non disponibile", (30, 140),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 1, cv2.LINE_AA)
            cv2.putText(img, "verifica picamera2 / /dev/video0", (30, 170),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 80, 80), 1, cv2.LINE_AA)
            _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])
            return buf.tobytes()

        _ph = _placeholder()
        _no_cam_streak = 0

        while True:
            try:
                frame = self._vision.get_annotated_frame() if self._vision else None
                if frame is None:
                    _no_cam_streak += 1
                    if _no_cam_streak % 20 == 1:   # ogni ~2s invia placeholder
                        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + _ph + b"\r\n"
                    time.sleep(0.1)
                    continue
                _no_cam_streak = 0
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
                if ok:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + buf.tobytes() + b"\r\n"
            except Exception as e:
                log.debug(f"MJPEG error: {e}")
                time.sleep(0.5)
            time.sleep(0.05)

    def _broadcast(self, data: dict):
        msg = json.dumps(data, default=str)
        with self._sse_lock:
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    # ── bus handlers ──────────────────────────────────────────────────────────
    def _on_mode(self, ev):
        shared.update(mode=ev.get("to", ""))
        self._broadcast({"type": "mode", "mode": ev.get("to")})

    def _on_scene(self, ev):
        shared.update(scene_description=ev.get("description", ""))
        self._broadcast({"type": "scene", "text": ev.get("description", "")})

    def _on_objects(self, ev):
        shared.update(detected_objects=ev.get("objects", ""))

    def _on_tts_start(self, ev):
        shared.update(tts_speaking=True)
        text = ev.get("text", "")
        shared.add_chat("spooky", text)
        self._broadcast({"type": "tts_start", "text": text})

    def _on_tts_finish(self, ev):
        shared.update(tts_speaking=False)
        self._broadcast({"type": "tts_stop"})

    def _on_obstacle(self, ev):
        shared.update(obstacle_blocked=True, distance_cm=ev.get("distance_cm", 0))
        self._broadcast({"type": "obstacle", "blocked": True, "cm": ev.get("distance_cm", 0)})

    def _on_obstacle_clear(self, ev):
        shared.update(obstacle_blocked=False, distance_cm=ev.get("distance_cm", 999))
        self._broadcast({"type": "obstacle", "blocked": False})

    def _on_heartbeat(self, ev):
        shared.update(distance_cm=ev.get("distance_cm", 999),
                      cpu_temp_c=ev.get("cpu_temp_c", 0),
                      ram_free_mb=ev.get("ram_free_mb", 8192))
        self._broadcast({"type": "heartbeat",
                         "dist": ev.get("distance_cm", 999),
                         "temp": ev.get("cpu_temp_c", 0),
                         "ram":  ev.get("ram_free_mb", 8192)})

    def _on_person_id(self, ev):
        shared.update(last_person=ev.get("display_name"),
                      face_detected=True, face_confidence=ev.get("confidence", 0))
        self._broadcast({"type": "person", "name": ev.get("display_name"),
                         "conf": ev.get("confidence", 0), "known": True})

    def _on_person_det(self, ev):
        shared.update(face_detected=True, face_confidence=ev.get("confidence", 0))

    def _on_person_lost(self, ev):
        shared.update(face_detected=False, face_confidence=0)
        self._broadcast({"type": "person_lost"})

    def _on_alert(self, ev):
        self._broadcast({"type": "alert", "level": ev.get("level"),
                         "reason": ev.get("reason")})

    def _on_mic_state(self, ev):
        state = ev.get("state", "idle")
        shared.update(mic_state=state)
        self._broadcast({"type": "mic", "state": state})

    def _on_wake(self, ev):
        shared.update(mic_state="listening")
        self._broadcast({"type": "mic", "state": "listening"})

    def _on_transcript(self, ev):
        text = ev.get("text", "")
        if text:
            shared.update(last_transcript=text)
            self._broadcast({"type": "transcript", "text": text})

    def _on_command_parsed(self, ev):
        cmd = ev.get("command", "")
        shared.update(mic_state="thinking", last_transcript="")
        shared.add_chat("user", cmd)
        self._broadcast({"type": "command", "text": cmd})

    def _on_llm_call(self, ev):
        self._broadcast({
            "type":    "llm_call",
            "trigger": ev.get("trigger", ""),
            "prompt":  ev.get("prompt", ""),
            "context": ev.get("context", ""),
            "reply":   ev.get("reply", ""),
            "time_ms": ev.get("time_ms", 0),
            "fallback":ev.get("fallback", False),
            "model":   ev.get("model", "?"),
        })

    def _on_any(self, ev):
        self._broadcast({"type": ev.type, **ev.payload})


# ── HTML ──────────────────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🕷️ Spooky</title>
<link href="https://fonts.googleapis.com/css2?family=Fredoka+One&family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0d0d0d;--card:#161616;--border:#222;
  --green:#39ff14;--yellow:#ffd60a;--red:#ff4444;--blue:#00c8ff;--purple:#c77dff;
  --text:#e8ffe8;--muted:#667;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Nunito',sans-serif;font-size:14px;overflow-x:hidden}

/* ── TOP BAR ── */
#topbar{
  display:flex;align-items:center;gap:.8rem;padding:.5rem 1rem;
  background:#0a0a0a;border-bottom:2px solid #1a1a1a;position:sticky;top:0;z-index:100
}
#topbar h1{font-family:'Fredoka One',cursive;font-size:1.5rem;color:var(--green);letter-spacing:1px}
.pill{
  padding:3px 12px;border-radius:20px;font-size:.75rem;font-weight:700;
  border:2px solid currentColor;font-family:'Fredoka One',cursive;letter-spacing:.5px
}
#mode-pill{color:var(--green);border-color:var(--green)}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.dot.on{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot.off{background:#333}
.dot.warn{background:var(--yellow);box-shadow:0 0 6px var(--yellow)}
.dot.red{background:var(--red);box-shadow:0 0 6px var(--red)}
.status-row{display:flex;align-items:center;gap:.4rem;font-size:.75rem;color:var(--muted)}
.status-row span{color:var(--text)}
#clock{margin-left:auto;font-family:'Fredoka One',cursive;font-size:1.1rem;color:var(--muted)}

/* ── LAYOUT ── */
.grid{display:grid;grid-template-columns:1fr 300px;gap:.7rem;padding:.7rem;min-height:calc(100vh - 52px)}
.left{display:flex;flex-direction:column;gap:.7rem}
.right{display:flex;flex-direction:column;gap:.7rem}

/* ── CARD ── */
.card{
  background:var(--card);border:2px solid var(--border);border-radius:16px;
  padding:.8rem;position:relative;overflow:hidden
}
.card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:3px;
  background:linear-gradient(90deg,var(--green),var(--blue));border-radius:16px 16px 0 0
}
.card h2{
  font-family:'Fredoka One',cursive;font-size:.95rem;color:var(--green);
  margin-bottom:.6rem;display:flex;align-items:center;gap:.4rem
}

/* ── CAMERA ── */
#cam-wrap{position:relative;border-radius:12px;overflow:hidden;background:#000;aspect-ratio:4/3}
#cam{width:100%;height:100%;object-fit:cover;display:block}
#cam-overlay{
  position:absolute;bottom:0;left:0;right:0;padding:.4rem .6rem;
  background:linear-gradient(transparent,rgba(0,0,0,.8));font-size:.75rem;color:#aaa
}
#face-badge{
  position:absolute;top:.5rem;right:.5rem;
  background:rgba(57,255,20,.15);border:2px solid var(--green);
  border-radius:8px;padding:2px 8px;font-size:.7rem;font-weight:700;color:var(--green);
  display:none
}

/* ── ROBOT FACE ── */
#robot-face{text-align:center;padding:.5rem 0}
#robot-svg{width:120px;height:120px}
#mood-text{font-family:'Fredoka One',cursive;font-size:.9rem;color:var(--purple);margin-top:.3rem}

/* ── MIC STATUS ── */
#mic-block{
  border-radius:12px;padding:.7rem;text-align:center;
  border:2px solid var(--border);transition:all .3s;cursor:default
}
#mic-block.idle{border-color:#333;background:#0f0f0f}
#mic-block.listening{border-color:var(--red);background:rgba(255,68,68,.08);animation:pulse-red 1s ease-in-out infinite}
#mic-block.thinking{border-color:var(--yellow);background:rgba(255,214,10,.06)}
#mic-icon{font-size:2rem;margin-bottom:.2rem}
#mic-label{font-family:'Fredoka One',cursive;font-size:.85rem}
#mic-block.idle #mic-label{color:var(--muted)}
#mic-block.listening #mic-label{color:var(--red)}
#mic-block.thinking #mic-label{color:var(--yellow)}
#transcript{font-size:.7rem;color:#888;min-height:1rem;margin-top:.3rem;font-style:italic}
@keyframes pulse-red{0%,100%{box-shadow:0 0 0 0 rgba(255,68,68,.4)}50%{box-shadow:0 0 0 10px rgba(255,68,68,0)}}

/* ── DRIVES ── */
.drive-row{display:flex;align-items:center;gap:.5rem;margin-bottom:.45rem}
.drive-label{font-size:.7rem;color:var(--muted);width:65px;text-align:right;flex-shrink:0}
.drive-bar{flex:1;height:8px;background:#1a1a1a;border-radius:4px;overflow:hidden}
.drive-fill{height:100%;border-radius:4px;transition:width .5s}
.drive-val{font-size:.7rem;font-weight:700;width:32px;text-align:right;flex-shrink:0}

/* ── SENSORS ── */
.sensor-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem}
.sensor{
  background:#0f0f0f;border:1px solid #1e1e1e;border-radius:10px;
  padding:.5rem;text-align:center
}
.sensor .sv{font-family:'Fredoka One',cursive;font-size:1.3rem;color:var(--green)}
.sensor .sl{font-size:.62rem;color:var(--muted);margin-top:2px}
.sensor.warn .sv{color:var(--yellow)}
.sensor.danger .sv{color:var(--red)}

/* ── PERSON ── */
#person-wrap{display:flex;align-items:center;gap:.6rem}
#person-avatar{font-size:2rem}
#person-name{font-family:'Fredoka One',cursive;font-size:1rem;color:var(--blue)}
#person-meta{font-size:.7rem;color:var(--muted)}

/* ── CHAT ── */
#chat{height:200px;overflow-y:auto;padding:.4rem;display:flex;flex-direction:column;gap:.4rem}
.bubble{max-width:85%;padding:.4rem .7rem;border-radius:12px;font-size:.82rem;line-height:1.4;word-break:break-word}
.bubble.spooky{background:rgba(57,255,20,.1);border:1px solid rgba(57,255,20,.25);color:var(--green);align-self:flex-start;border-radius:4px 12px 12px 12px}
.bubble.spooky::before{content:'🕷️ ';font-size:.8rem}
.bubble.user{background:rgba(0,200,255,.1);border:1px solid rgba(0,200,255,.25);color:var(--blue);align-self:flex-end;border-radius:12px 4px 12px 12px}
.bubble.user::before{content:'👤 ';font-size:.8rem}
.bubble.system{background:#1a1a1a;color:var(--muted);align-self:center;border-radius:8px;font-style:italic;font-size:.75rem}
.chat-bar{display:flex;gap:.4rem;margin-top:.5rem}
.chat-bar input{
  flex:1;background:#0f0f0f;border:2px solid #222;color:var(--text);
  padding:.35rem .6rem;border-radius:10px;font-family:'Nunito',sans-serif;font-size:.82rem;outline:none
}
.chat-bar input:focus{border-color:var(--green)}
.chat-bar button{
  background:var(--green);color:#000;border:none;padding:.35rem .8rem;
  border-radius:10px;font-family:'Fredoka One',cursive;cursor:pointer;font-size:.85rem
}

/* ── MODE BUTTONS ── */
.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:.4rem}
.mbtn{
  background:#0f0f0f;border:2px solid #222;color:#888;
  padding:.45rem .3rem;border-radius:10px;font-family:'Fredoka One',cursive;
  font-size:.7rem;cursor:pointer;transition:all .2s;text-align:center
}
.mbtn:hover{border-color:var(--green);color:var(--green)}
.mbtn.active{background:rgba(57,255,20,.1);border-color:var(--green);color:var(--green)}
.mbtn.night{color:var(--purple)}
.mbtn.night.active{background:rgba(199,125,255,.1);border-color:var(--purple)}

/* ── QUICK ACTIONS ── */
.qa-row{display:flex;gap:.4rem;flex-wrap:wrap}
.qbtn{
  background:#0f0f0f;border:2px solid #222;color:var(--muted);
  padding:.3rem .6rem;border-radius:8px;font-size:.72rem;cursor:pointer;
  font-family:'Fredoka One',cursive;transition:all .2s
}
.qbtn:hover{border-color:var(--blue);color:var(--blue)}

/* ── LOG ── */
#log{height:130px;overflow-y:auto;font-size:.68rem;font-family:monospace}
.le{padding:1px 0;border-bottom:1px solid #111;color:#555;word-break:break-all}
.le.alert{color:var(--red)}
.le.person{color:var(--blue)}
.le.command{color:var(--yellow)}
.le.tts{color:var(--green)}

/* ── LLM STREAM ── */
#llm-stream{height:280px;overflow-y:auto;display:flex;flex-direction:column;gap:.35rem}
.llm-e{border-left:3px solid var(--green);padding:.3rem .5rem;background:#111;border-radius:0 4px 4px 0}
.llm-e.fb{border-left-color:#333}
.llm-e .llm-hd{font-size:.58rem;color:var(--muted);display:flex;gap:.4rem;margin-bottom:.2rem}
.llm-e .llm-hd .llm-tag{background:#222;border-radius:3px;padding:1px 4px;text-transform:uppercase;letter-spacing:.05em}
.llm-e .llm-hd .llm-tag.real{color:var(--green)}
.llm-e .llm-hd .llm-tag.fall{color:#444}
.llm-e .llm-prompt{color:var(--blue);font-size:.68rem;margin-bottom:.2rem}
.llm-e .llm-ctx{color:#555;font-size:.6rem;font-style:italic;margin-bottom:.15rem}
.llm-e .llm-reply{color:var(--green);font-size:.72rem;font-weight:600}
.llm-e.fb .llm-reply{color:#555}
.llm-e .llm-meta{font-size:.57rem;color:#333;margin-top:.15rem}

/* ── FACTS TABLE ── */
#facts-tbl{width:100%;border-collapse:collapse;font-size:.72rem}
#facts-tbl th{color:var(--blue);border-bottom:1px solid #222;padding:2px 4px;text-align:left}
#facts-tbl td{padding:2px 4px;border-bottom:1px solid #111;color:#aaa}
#facts-tbl td:last-child{color:var(--green);font-weight:700}

/* ── SPEAKING PULSE ── */
#speaking-wave{display:none;gap:3px;align-items:flex-end;height:16px;margin-left:.4rem}
#speaking-wave.on{display:inline-flex}
.bar-w{width:3px;background:var(--green);border-radius:2px;animation:wave .6s ease-in-out infinite}
.bar-w:nth-child(2){animation-delay:.1s;height:8px}
.bar-w:nth-child(3){animation-delay:.2s}
.bar-w:nth-child(4){animation-delay:.3s;height:10px}
.bar-w:nth-child(5){animation-delay:.4s}
@keyframes wave{0%,100%{height:4px}50%{height:14px}}

@media(max-width:720px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<!-- TOP BAR -->
<div id="topbar">
  <h1>🕷️ SPOOKY</h1>
  <span class="pill" id="mode-pill">companion_day</span>
  <div class="status-row">
    <span class="dot" id="dot-cam" title="Camera"></span><span>CAM</span>
    <span class="dot" id="dot-mic" title="Mic"></span><span>MIC</span>
    <span class="dot" id="dot-motor" title="Motor"></span><span>MOT</span>
    <span class="dot" id="dot-obs" title="Obstacle"></span><span>OBS</span>
  </div>
  <div id="speaking-wave" title="Sta parlando">
    <div class="bar-w" style="height:6px"></div>
    <div class="bar-w"></div>
    <div class="bar-w" style="height:12px"></div>
    <div class="bar-w"></div>
    <div class="bar-w" style="height:6px"></div>
  </div>
  <span id="clock">--:--:--</span>
</div>

<div class="grid">

<!-- ═══ LEFT ═══ -->
<div class="left">

  <!-- Camera -->
  <div class="card">
    <h2>📷 Camera</h2>
    <div id="cam-wrap">
      <img id="cam" src="/camera" alt="camera feed" onerror="this.style.opacity=.3">
      <div id="face-badge">👤 VOLTO</div>
      <div id="cam-overlay" id="scene-txt">—</div>
    </div>
  </div>

  <!-- Visione attiva -->
  <div class="card">
    <h2>👁️ Visione <span id="vision-model-lbl" style="font-size:.6rem;color:var(--muted);font-family:'Nunito'"></span>
      <span id="vision-age" style="font-size:.58rem;color:var(--muted);float:right;font-family:'Nunito'">—</span>
    </h2>
    <div id="vision-scene" style="font-size:.8rem;color:var(--green);min-height:2.2rem;line-height:1.4;margin-bottom:.4rem">—</div>
    <div style="font-size:.65rem;color:var(--muted);margin-bottom:.25rem">Oggetti rilevati:</div>
    <div id="vision-objects" style="font-size:.75rem;color:var(--blue);min-height:1.4rem;word-break:break-word">—</div>
  </div>

  <!-- Sensors -->
  <div class="card">
    <h2>📡 Sensori</h2>
    <div class="sensor-grid">
      <div class="sensor" id="s-dist">
        <div class="sv" id="v-dist">—</div>
        <div class="sl">Distanza cm</div>
      </div>
      <div class="sensor" id="s-temp">
        <div class="sv" id="v-temp">—</div>
        <div class="sl">CPU °C</div>
      </div>
      <div class="sensor" id="s-ram">
        <div class="sv" id="v-ram">—</div>
        <div class="sl">RAM MB</div>
      </div>
      <div class="sensor" id="s-pitch">
        <div class="sv" id="v-pitch">—</div>
        <div class="sl">Pitch °</div>
      </div>
      <div class="sensor" id="s-roll">
        <div class="sv" id="v-roll">—</div>
        <div class="sl">Roll °</div>
      </div>
      <div class="sensor" id="s-edge">
        <div class="sv" id="v-edge" style="font-size:.9rem">OK</div>
        <div class="sl">Bordo</div>
      </div>
    </div>
    <div style="font-size:.58rem;color:var(--muted);text-align:right;margin-top:.2rem">
      heartbeat <span id="hb-age">—</span>
    </div>
  </div>

  <!-- Mappa ambiente -->
  <div class="card" id="map-card">
    <h2>🗺️ Mappa ambiente
      <button id="btn-scan" onclick="startScan()" style="float:right;padding:.2rem .7rem;border-radius:1rem;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:.75rem">▶ Scansiona</button>
    </h2>
    <div style="display:flex;justify-content:center;align-items:center;min-height:180px">
      <svg id="radar-svg" width="200" height="200" viewBox="-110 -110 220 220">
        <!-- Cerchi di riferimento -->
        <circle cx="0" cy="0" r="30"  fill="none" stroke="#333" stroke-width="0.5"/>
        <circle cx="0" cy="0" r="60"  fill="none" stroke="#333" stroke-width="0.5"/>
        <circle cx="0" cy="0" r="90"  fill="none" stroke="#333" stroke-width="0.5"/>
        <!-- Assi -->
        <line x1="0" y1="-95" x2="0" y2="95"  stroke="#333" stroke-width="0.5"/>
        <line x1="-95" y1="0" x2="95" y2="0"  stroke="#333" stroke-width="0.5"/>
        <!-- Etichette distanza -->
        <text x="32" y="-26" fill="#555" font-size="7">50cm</text>
        <text x="62" y="-56" fill="#555" font-size="7">100cm</text>
        <!-- Robot al centro -->
        <polygon points="0,-10 6,6 -6,6" fill="var(--accent)" opacity="0.9"/>
        <!-- Settori scan (aggiornati da JS) -->
        <g id="radar-sectors"></g>
        <!-- Punti ostacolo -->
        <g id="radar-dots"></g>
        <!-- Freccia direzione -->
        <text x="-4" y="-98" fill="var(--muted)" font-size="8">N</text>
        <text id="scan-status" x="-50" y="108" fill="var(--muted)" font-size="8">Premi Scansiona</text>
      </svg>
    </div>
  </div>

  <!-- Persona -->
  <div class="card">
    <h2>👤 Persona rilevata</h2>
    <div id="person-wrap">
      <div id="person-avatar">❓</div>
      <div>
        <div id="person-name" style="color:var(--muted)">Nessuno</div>
        <div id="person-meta"></div>
      </div>
    </div>
  </div>

  <!-- Memoria fatti -->
  <div class="card">
    <h2>🧠 Memoria semantica</h2>
    <table id="facts-tbl">
      <thead><tr><th>Chiave</th><th>Valore</th><th>Conf</th></tr></thead>
      <tbody id="facts-body"></tbody>
    </table>
  </div>

</div>

<!-- ═══ RIGHT ═══ -->
<div class="right">

  <!-- Robot face + mood -->
  <div class="card">
    <h2>🤖 Spooky</h2>
    <div id="robot-face">
      <svg id="robot-svg" viewBox="0 0 120 120">
        <!-- head -->
        <rect x="20" y="30" width="80" height="65" rx="15" fill="#1a1a1a" stroke="#39ff14" stroke-width="2"/>
        <!-- eyes -->
        <circle id="eye-l" cx="42" cy="58" r="12" fill="#0d0d0d" stroke="#39ff14" stroke-width="2"/>
        <circle id="eye-r" cx="78" cy="58" r="12" fill="#0d0d0d" stroke="#39ff14" stroke-width="2"/>
        <circle id="pupil-l" cx="42" cy="58" r="5" fill="#39ff14"/>
        <circle id="pupil-r" cx="78" cy="58" r="5" fill="#39ff14"/>
        <!-- mouth -->
        <path id="mouth" d="M 40 82 Q 60 92 80 82" fill="none" stroke="#39ff14" stroke-width="2.5" stroke-linecap="round"/>
        <!-- antenna -->
        <line x1="60" y1="30" x2="60" y2="15" stroke="#39ff14" stroke-width="2"/>
        <circle id="antenna-dot" cx="60" cy="12" r="4" fill="#39ff14"/>
        <!-- cheek blush -->
        <ellipse id="blush-l" cx="30" cy="72" rx="8" ry="5" fill="#ff69b4" opacity="0"/>
        <ellipse id="blush-r" cx="90" cy="72" rx="8" ry="5" fill="#ff69b4" opacity="0"/>
      </svg>
      <div id="mood-text">😊 content</div>
    </div>
  </div>

  <!-- Mic status -->
  <div class="card">
    <h2>🎙️ Microfono</h2>
    <div id="mic-block" class="idle">
      <div id="mic-icon">😴</div>
      <div id="mic-label">IN ASCOLTO (wake word)</div>
      <div id="transcript"></div>
    </div>
  </div>

  <!-- Drives -->
  <div class="card">
    <h2>⚡ Drive interni</h2>
    <div id="drives-container">
      <!-- filled by JS -->
    </div>
  </div>

  <!-- Modalità -->
  <div class="card">
    <h2>🎮 Modalità</h2>
    <div class="mode-grid">
      <button class="mbtn" id="mb-companion_day"   onclick="setMode('companion_day')">🌞 Companion</button>
      <button class="mbtn" id="mb-focus_assistant" onclick="setMode('focus_assistant')">🎯 Focus</button>
      <button class="mbtn" id="mb-idle_observer"   onclick="setMode('idle_observer')">👁️ Observer</button>
      <button class="mbtn night" id="mb-night_watch" onclick="setMode('night_watch')">🌙 Night Watch</button>
    </div>
  </div>

  <!-- Controllo motori -->
  <div class="card">
    <h2>🕹️ Motori</h2>
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem;max-width:220px;margin:0 auto .5rem">
      <div></div>
      <button class="mbtn" onmousedown="motorHold('forward')" onmouseup="motorStop()" ontouchstart="motorHold('forward')" ontouchend="motorStop()">▲</button>
      <div></div>
      <button class="mbtn" onmousedown="motorHold('left')" onmouseup="motorStop()" ontouchstart="motorHold('left')" ontouchend="motorStop()">◀</button>
      <button class="mbtn" onclick="motorCmd('stop')">■</button>
      <button class="mbtn" onmousedown="motorHold('right')" onmouseup="motorStop()" ontouchstart="motorHold('right')" ontouchend="motorStop()">▶</button>
      <div></div>
      <button class="mbtn" onmousedown="motorHold('backward')" onmouseup="motorStop()" ontouchstart="motorHold('backward')" ontouchend="motorStop()">▼</button>
      <div></div>
    </div>
    <div style="display:flex;gap:.4rem;justify-content:center">
      <button class="mbtn" onclick="motorCmd('wave')">👋 Wave</button>
      <button class="mbtn" onclick="motorCmd('center')">🎯 Centro</button>
    </div>
  </div>

  <!-- Chat -->
  <div class="card" style="flex:1">
    <h2>💬 Chat</h2>
    <div id="chat"></div>
    <div class="chat-bar">
      <input id="cmd-in" placeholder="Scrivi a Spooky…" onkeydown="if(event.key==='Enter')sendCmd()">
      <button onclick="sendCmd()">▶</button>
    </div>
    <div class="qa-row" style="margin-top:.4rem">
      <button class="qbtn" onclick="sendRaw('cosa vedi?')">👁 Scena</button>
      <button class="qbtn" onclick="sendRaw('cosa ricordi?')">🧠 Memoria</button>
      <button class="qbtn" onclick="sendRaw('ciao')">👋 Saluta</button>
      <button class="qbtn" onclick="summarize()">💾 Riassumi</button>
    </div>
  </div>

  <!-- Log -->
  <div class="card">
    <h2>📜 Log <span id="log-n" style="font-size:.65rem;color:var(--muted);font-family:'Nunito'"></span></h2>
    <div id="log"></div>
  </div>

  <!-- Flusso LLM -->
  <div class="card">
    <h2>🧠 Flusso LLM <span id="llm-model-lbl" style="font-size:.62rem;color:var(--muted);font-family:'Nunito'"></span></h2>
    <div id="llm-stream"></div>
  </div>

</div>
</div>

<script>
/* ── clock ── */
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString('it')},1000);

/* ── FACE EXPRESSIONS ── */
const EXPR = {
  content:   {m:'M 40 82 Q 60 92 80 82', pl:'#39ff14', pupilR:5, blush:0},
  happy:     {m:'M 35 78 Q 60 96 85 78', pl:'#39ff14', pupilR:6, blush:.3},
  excited:   {m:'M 35 75 Q 60 98 85 75', pl:'#ffd60a', pupilR:7, blush:.5},
  thinking:  {m:'M 45 85 Q 60 85 75 85', pl:'#00c8ff', pupilR:4, blush:0},
  listening: {m:'M 42 84 Q 60 90 78 84', pl:'#ff4444', pupilR:6, blush:0},
  speaking:  {m:'M 42 80 Q 60 95 78 80', pl:'#39ff14', pupilR:5, blush:.1},
  idle:      {m:'M 42 84 Q 60 84 78 84', pl:'#555',    pupilR:4, blush:0},
  curious:   {m:'M 42 84 Q 55 90 78 80', pl:'#c77dff', pupilR:5, blush:0},
  sleepy:    {m:'M 45 86 Q 60 84 75 86', pl:'#555',    pupilR:3, blush:0},
};
function setExpr(name){
  const e=EXPR[name]||EXPR.content;
  document.getElementById('mouth').setAttribute('d',e.m);
  ['pupil-l','pupil-r'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.setAttribute('r',e.pupilR);
  });
  ['eye-l','eye-r'].forEach(id=>{
    const el=document.getElementById(id);
    if(el) el.setAttribute('stroke',e.pl);
  });
  document.getElementById('blush-l').style.opacity=e.blush;
  document.getElementById('blush-r').style.opacity=e.blush;
}

/* ── antenna pulse ── */
let antTick=0;
setInterval(()=>{
  antTick++;
  const a=document.getElementById('antenna-dot');
  if(a) a.style.opacity=(.5+Math.sin(antTick*.3)*.5).toFixed(2);
},100);

/* ── DRIVES ── */
const DRIVE_DEFS=[
  {k:'energy',       lbl:'Energia',   col:'#39ff14'},
  {k:'social_drive', lbl:'Social',    col:'#00c8ff'},
  {k:'curiosity',    lbl:'Curiosità', col:'#c77dff'},
  {k:'attention',    lbl:'Attenzione',col:'#ffd60a'},
  {k:'interaction_fatigue', lbl:'Fatica', col:'#ff4444', invert:true},
];
function renderDrives(d){
  const c=document.getElementById('drives-container');
  c.innerHTML=DRIVE_DEFS.map(({k,lbl,col,invert})=>{
    const v=d[k]||0;
    const pct=Math.round(v*100);
    const warn=invert?v>.6:v<.2;
    const fill=warn?'#ff4444':col;
    return `<div class="drive-row">
      <div class="drive-label">${lbl}</div>
      <div class="drive-bar"><div class="drive-fill" style="width:${pct}%;background:${fill}"></div></div>
      <div class="drive-val" style="color:${fill}">${pct}%</div>
    </div>`;
  }).join('');
}

/* ── MODE ── */
let _curMode='';
function applyMode(m){
  if(m===_curMode)return; _curMode=m;
  document.querySelectorAll('.mbtn').forEach(b=>b.classList.remove('active'));
  const b=document.getElementById('mb-'+m); if(b)b.classList.add('active');
  document.getElementById('mode-pill').textContent=m;
  // face expression
  const exprMap={companion_day:'content',focus_assistant:'thinking',
                 idle_observer:'idle',night_watch:'sleepy',safe_shutdown:'sleepy'};
  setExpr(exprMap[m]||'content');
}

/* ── MIC STATE ── */
function applyMic(state, transcript){
  const blk=document.getElementById('mic-block');
  const icon=document.getElementById('mic-icon');
  const lbl=document.getElementById('mic-label');
  blk.className=state;
  const dot=document.getElementById('dot-mic');
  if(state==='listening'){
    icon.textContent='🎤'; lbl.textContent='ASCOLTANDO!';
    dot.className='dot red';
    setExpr('listening');
  } else if(state==='thinking'){
    icon.textContent='🤔'; lbl.textContent='ELABORANDO…';
    dot.className='dot warn';
    setExpr('thinking');
  } else {
    icon.textContent='😴'; lbl.textContent='IN ASCOLTO (wake word)';
    dot.className='dot on';
  }
  if(transcript!=null) document.getElementById('transcript').textContent=transcript||'';
}

/* ── VISION PANEL ── */
let _vision={scene:'',objects:'',lastTs:0};
setInterval(()=>{
  const el=document.getElementById('vision-age');
  if(!el||_vision.lastTs===0) return;
  const s=Math.round((Date.now()-_vision.lastTs)/1000);
  el.textContent='aggiornato '+s+'s fa';
  el.style.color=s>60?'var(--red)':s>30?'var(--yellow)':'var(--green)';
},1000);
function applyVision(scene,objects,model){
  _vision={scene:scene||'',objects:objects||'',lastTs:Date.now()};
  const se=document.getElementById('vision-scene');
  const oe=document.getElementById('vision-objects');
  const ml=document.getElementById('vision-model-lbl');
  if(se && scene) se.textContent=scene;
  if(oe && objects) oe.textContent=objects;
  if(ml && model) ml.textContent='['+model+']';
}

/* ── SENSORS ── */
function setSensor(id,valId,val,warnFn,dangerFn){
  const v=document.getElementById(valId);
  const s=document.getElementById(id);
  if(v) v.textContent=val;
  if(!s) return;
  s.className='sensor'+(dangerFn&&dangerFn()?' danger':warnFn&&warnFn()?' warn':'');
}

/* ── SENSOR STATE ── */
let _sens={dist:999,temp:0,ram:0,lastTs:0};
setInterval(()=>{
  const el=document.getElementById('hb-age');
  if(!el) return;
  if(_sens.lastTs===0){el.textContent='nessuno';return;}
  const s=Math.round((Date.now()-_sens.lastTs)/1000);
  el.textContent=s+'s fa';
  el.style.color=s>10?'var(--red)':s>5?'var(--yellow)':'var(--green)';
},1000);
function applySensors(dist,temp,ram,pitch,roll){
  pitch=pitch||0; roll=roll||0;
  _sens={dist,temp,ram,pitch,roll,lastTs:Date.now()};
  setSensor('s-dist','v-dist',dist>=990?'—':dist.toFixed(0)+' cm',
    ()=>dist<40, ()=>dist<20);
  setSensor('s-temp','v-temp',temp.toFixed(1)+'°C',
    ()=>temp>65, ()=>temp>75);
  setSensor('s-ram','v-ram',ram+' MB',
    ()=>ram<600, ()=>ram<300);
  // Pitch / Roll
  const pEl=document.getElementById('v-pitch');
  const rEl=document.getElementById('v-roll');
  const eEl=document.getElementById('v-edge');
  const sP=document.getElementById('s-pitch');
  const sR=document.getElementById('s-roll');
  const sE=document.getElementById('s-edge');
  if(pEl) pEl.textContent=pitch.toFixed(1)+'°';
  if(rEl) rEl.textContent=roll.toFixed(1)+'°';
  const edgeFwd  = pitch < -10;
  const edgeBck  = pitch >  10;
  const edgeLft  = roll  < -10;
  const edgeRgt  = roll  >  10;
  const onEdge   = edgeFwd||edgeBck||edgeLft||edgeRgt;
  if(eEl) eEl.textContent = onEdge ? '⚠️ BORDO' : 'OK';
  if(sP) sP.className='sensor'+(Math.abs(pitch)>10?' danger':Math.abs(pitch)>6?' warn':'');
  if(sR) sR.className='sensor'+(Math.abs(roll) >10?' danger':Math.abs(roll) >6?' warn':'');
  if(sE) sE.className='sensor'+(onEdge?' danger':'');
  document.getElementById('dot-obs').className=
    'dot '+(dist<20?'red':dist<40?'warn':'on');
}
/* Polling diretto /api/sensors ogni 2s — fonte primaria affidabile */
async function fetchSensors(){
  try{
    const r=await fetch('/api/sensors'); const d=await r.json();
    const dist=(d.dist!=null)?d.dist:999;
    const temp=(d.temp!=null)?d.temp:0;
    const ram=(d.ram!=null)?d.ram:0;
    applySensors(dist,temp,ram,d.pitch||0,d.roll||0);
  }catch(e){}
}
fetchSensors(); setInterval(fetchSensors,2000);

/* ── STATE POLL ── */
async function fetchState(){
  try{
    const r=await fetch('/api/state'); const d=await r.json();
    applyMode(d.mode||'');
    const dr=d.drives||{};
    renderDrives(dr);
    const mood=(dr.mood||'content').toLowerCase();
    document.getElementById('mood-text').textContent='😊 '+mood;
    // dots
    document.getElementById('dot-cam').className='dot on';
    document.getElementById('dot-motor').className='dot on';
    // mic
    applyMic(d.mic_state||'idle', d.last_transcript||'');
    // speaking
    const sw=document.getElementById('speaking-wave');
    sw.className='speaking-wave'+(d.tts_speaking?' on':'');
  }catch(e){}
}
fetchState(); setInterval(fetchState,5000);

/* ── VISION POLL (fallback se SSE mancante) ── */
async function fetchVision(){
  try{
    const r=await fetch('/api/vision'); const d=await r.json();
    if(d.scene||d.objects) applyVision(d.scene||'',d.objects||'',d.model||'');
    const ml=document.getElementById('vision-model-lbl');
    if(ml && d.model) ml.textContent='['+(d.model||'?')+']';
    const co=document.getElementById('dot-cam');
    if(co) co.className='dot '+(d.camera&&d.camera!=='none'&&d.camera!=='sim'?'on':'warn');
  }catch(e){}
}
fetchVision(); setInterval(fetchVision,5000);

/* ── FACTS POLL ── */
async function fetchFacts(){
  try{
    const r=await fetch('/api/memory'); const d=await r.json();
    const tb=document.getElementById('facts-body');
    tb.innerHTML=(d.facts||[]).slice(0,8).map(f=>
      `<tr><td>${f.key}</td><td>${f.value}</td><td>${Math.round(f.confidence*100)}%</td></tr>`
    ).join('') || '<tr><td colspan="3" style="color:#555;text-align:center">vuota</td></tr>';
  }catch(e){}
}
fetchFacts(); setInterval(fetchFacts,10000);

/* ── SSE ── */
let logN=0;
const es=new EventSource('/stream');
es.onmessage=e=>{
  try{
    const d=JSON.parse(e.data);
    if(d.type==='ping') return;

    if(d.type==='mode')    applyMode(d.mode||'');
    if(d.type==='mic')     applyMic(d.state);
    if(d.type==='transcript') applyMic(null, d.text);
    if(d.type==='tts_start'){
      addBubble('spooky',d.text);
      document.getElementById('speaking-wave').className='speaking-wave on';
      setExpr('speaking');
    }
    if(d.type==='tts_stop'){
      document.getElementById('speaking-wave').className='speaking-wave';
      setExpr(_curMode==='night_watch'?'sleepy':'content');
    }
    if(d.type==='command') addBubble('user',d.text);
    if(d.type==='scene'||d.type==='scene_analyzed'){
      const desc=d.text||d.description||'';
      if(desc){
        document.getElementById('cam-overlay').textContent=desc;
        applyVision(desc, _vision.objects);
      }
    }
    if(d.type==='objects_detected'){
      const obj=d.text||d.objects||'';
      if(obj){
        document.getElementById('cam-overlay').textContent='🔍 '+obj;
        applyVision(_vision.scene, obj);
      }
    }
    if(d.type==='person'){
      document.getElementById('person-avatar').textContent='😊';
      document.getElementById('person-name').textContent=d.name||'Sconosciuto';
      document.getElementById('person-name').style.color=d.known?'var(--blue)':'var(--yellow)';
      document.getElementById('person-meta').textContent=d.conf?'conf: '+(d.conf*100).toFixed(0)+'%':'';
      const fb=document.getElementById('face-badge');
      fb.style.display='block'; fb.textContent='👤 '+(d.name||'?');
      setExpr('happy');
    }
    if(d.type==='person_lost'){
      document.getElementById('face-badge').style.display='none';
      document.getElementById('person-avatar').textContent='❓';
      document.getElementById('person-name').textContent='Nessuno';
      document.getElementById('person-name').style.color='var(--muted)';
    }
    if(d.type==='heartbeat'){
      const dist=(d.dist!=null)?d.dist:999;
      const temp=(d.temp!=null)?d.temp:0;
      const ram=(d.ram!=null)?d.ram:0;
      applySensors(dist,temp,ram,d.pitch_deg||0,d.roll_deg||0);
    }
    if(d.type==='obstacle'){
      document.getElementById('dot-obs').className='dot '+(d.blocked?'red':'on');
    }
    if(d.type==='alert'){
      addLog('[ALERT L'+d.level+'] '+d.reason,'alert');
    }
    if(d.type==='scan_complete'){
      renderRadar(d.readings||[]);
      const btn=document.getElementById('btn-scan');
      if(btn) btn.disabled=false;
    }
    if(d.type==='llm_call'){
      const stream=document.getElementById('llm-stream');
      if(stream){
        const fb=!!d.fallback;
        const ts=new Date().toLocaleTimeString('it');
        const el=document.createElement('div');
        el.className='llm-e'+(fb?' fb':'');
        const tagCls=fb?'fall':'real';
        const tagTxt=fb?'FALLBACK':'LLM';
        el.innerHTML=
          '<div class="llm-hd">'
          +'<span>'+ts+'</span>'
          +(d.trigger?'<span class="llm-tag">'+d.trigger+'</span>':'')
          +'<span class="llm-tag '+tagCls+'">'+tagTxt+'</span>'
          +(d.time_ms?'<span>'+d.time_ms+'ms</span>':'')
          +'</div>'
          +(d.prompt?'<div class="llm-prompt">▶ '+d.prompt+'</div>':'')
          +(d.context?'<div class="llm-ctx">ctx: '+d.context+'</div>':'')
          +'<div class="llm-reply">◀ '+(d.reply||'—')+'</div>'
          +'<div class="llm-meta">'+d.model+'</div>';
        stream.insertBefore(el,stream.firstChild);
        while(stream.children.length>25) stream.lastChild.remove();
        // aggiorna label modello
        const lbl=document.getElementById('llm-model-lbl');
        if(lbl && d.model) lbl.textContent='['+d.model+']';
      }
    }

    // log
    const skip=['ping','heartbeat','speech_transcribed','person_detected','scan_complete','llm_call','scene_analyzed','objects_detected'].includes(d.type);
    if(!skip){
      const cls=d.type==='alert'?'alert':d.type.startsWith('tts')?'tts':
               d.type==='command'||d.type==='command_parsed'?'command':
               d.type.startsWith('person')?'person':'';
      addLog('['+d.type+'] '+JSON.stringify(d).replace(/{"type":"[^"]+",?/,'').slice(0,60),cls);
    }
  }catch(err){}
};
es.onerror=()=>console.warn('SSE disconnected');

/* ── RADAR MAP ── */
const MAX_RADAR_R = 90;   // px → corrisponde a 200cm
const MAX_DIST_CM = 200;  // distanza massima visualizzata

function angleToXY(angleDeg, r){
  // 0° = fronte (alto nell'SVG = -y)
  const rad = (angleDeg - 90) * Math.PI / 180;
  return {x: r * Math.cos(rad), y: r * Math.sin(rad)};
}

function renderRadar(readings){
  const sectors = document.getElementById('radar-sectors');
  const dots    = document.getElementById('radar-dots');
  const status  = document.getElementById('scan-status');
  if(!sectors || !readings.length) return;

  sectors.innerHTML = '';
  dots.innerHTML    = '';

  const n = readings.length;
  const stepDeg = 360 / n;

  readings.forEach((r, i) => {
    const dist = Math.min(r.dist >= 990 ? MAX_DIST_CM : r.dist, MAX_DIST_CM);
    const frac = dist / MAX_DIST_CM;
    const pr   = frac * MAX_RADAR_R;    // pixel radius
    const a1   = i * stepDeg;
    const a2   = (i + 1) * stepDeg;
    const p0   = {x:0, y:0};
    const p1   = angleToXY(a1, pr);
    const p2   = angleToXY(a2, pr);

    // Colore: verde=lontano, giallo=medio, rosso=vicino
    const color = dist < 30 ? '#e74c3c' : dist < 80 ? '#f1c40f' : '#2ecc71';

    // Settore a torta
    const large = stepDeg > 180 ? 1 : 0;
    const d = `M0,0 L${p1.x.toFixed(1)},${p1.y.toFixed(1)} A${pr.toFixed(1)},${pr.toFixed(1)} 0 ${large} 1 ${p2.x.toFixed(1)},${p2.y.toFixed(1)} Z`;
    const path = document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d', d);
    path.setAttribute('fill', color);
    path.setAttribute('opacity', '0.35');
    path.setAttribute('stroke', color);
    path.setAttribute('stroke-width', '0.5');
    sectors.appendChild(path);

    // Punto ostacolo (solo se non "infinito")
    if(r.dist < 990){
      const mid = angleToXY(a1 + stepDeg/2, pr);
      const dot = document.createElementNS('http://www.w3.org/2000/svg','circle');
      dot.setAttribute('cx', mid.x.toFixed(1));
      dot.setAttribute('cy', mid.y.toFixed(1));
      dot.setAttribute('r', '3');
      dot.setAttribute('fill', color);
      dots.appendChild(dot);
    }
  });

  if(status) status.textContent = `${n} letture — ${new Date().toLocaleTimeString('it')}`;
}

async function startScan(){
  const btn = document.getElementById('btn-scan');
  const status = document.getElementById('scan-status');
  if(btn) btn.disabled = true;
  if(status) status.textContent = 'Scansione in corso…';
  try{
    await fetch('/api/scan', {method:'POST'});
    // risultato arriva via SSE scan_complete
  } catch(e){
    if(status) status.textContent = 'Errore scansione';
  } finally {
    setTimeout(()=>{ if(btn) btn.disabled=false; }, 15000);
  }
}

// Carica ultima scansione all'avvio
fetch('/api/scan').then(r=>r.json()).then(d=>{ if(d.readings&&d.readings.length) renderRadar(d.readings); });

/* ── LOG ── */
function addLog(txt,cls){
  const l=document.getElementById('log');
  const d=document.createElement('div');
  d.className='le '+(cls||'');
  d.textContent=new Date().toLocaleTimeString('it')+' '+txt;
  l.prepend(d);
  while(l.children.length>80) l.removeChild(l.lastChild);
  logN++;
  document.getElementById('log-n').textContent='('+logN+')';
}

/* ── CHAT BUBBLES ── */
function addBubble(role,text){
  if(!text) return;
  const c=document.getElementById('chat');
  const d=document.createElement('div');
  d.className='bubble '+role;
  d.textContent=text;
  c.appendChild(d);
  c.scrollTop=c.scrollHeight;
}

async function sendCmd(){
  const el=document.getElementById('cmd-in');
  const cmd=el.value.trim(); if(!cmd) return;
  el.value='';
  addBubble('user',cmd);
  await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
}
async function sendRaw(cmd){ addBubble('user',cmd); await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})}); }
async function setMode(m){ await fetch('/api/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:m})}); }
async function summarize(){ await fetch('/api/summarize',{method:'POST'}); addBubble('system','Riassunto avviato…'); }

let _motorTimer=null;
async function motorCmd(action,speed=50,duration=0.8){
  await fetch('/api/motor',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,speed,duration})});
}
function motorHold(action){
  motorCmd(action,55,2.0);
  _motorTimer=setInterval(()=>motorCmd(action,55,2.0),2100);
}
function motorStop(){
  clearInterval(_motorTimer); _motorTimer=null;
  motorCmd('stop');
}
</script>
</body>
</html>
"""
