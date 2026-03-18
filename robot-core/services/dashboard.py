"""
services/dashboard.py — Flask inspection dashboard.

Provides:
  GET  /                     → HTML status page
  GET  /api/state            → JSON snapshot of all internal state
  GET  /api/persons          → JSON list of known persons
  GET  /api/memory           → JSON recent episodes + facts
  GET  /api/alerts           → JSON recent night watch alerts
  POST /api/command          → inject a text command  {"command": "..."}
  POST /api/mode             → switch mode             {"mode": "night_watch"}
  GET  /stream               → SSE stream of bus events

Run on port 5000 by default. Bind to 0.0.0.0 so you can reach it from
another machine on the same network (e.g. your laptop while RPi is on desk).

Privacy: no authentication by default. Bind to 127.0.0.1 in
production unless you add auth. Configurable via cfg.get("dashboard.host").
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


# ── Shared state object (populated by services, read by dashboard) ────────────

class SharedState:
    """
    Thread-safe bag of current robot state.
    Each service updates its slice; dashboard reads the whole thing.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = {
            "mode":               "companion_day",
            "mood":               "content",
            "energy":             1.0,
            "social_drive":       0.5,
            "curiosity":          0.6,
            "interaction_fatigue":0.0,
            "scene_description":  "",
            "detected_objects":   "",
            "tts_speaking":       False,
            "obstacle_blocked":   False,
            "distance_cm":        999.0,
            "cpu_temp_c":         0.0,
            "ram_free_mb":        8192,
            "chat_history":       [],    # list of {"role", "text", "ts"}
            "last_person":        None,
        }

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)

    def add_chat(self, role: str, text: str) -> None:
        with self._lock:
            self._data["chat_history"].append({
                "role": role,
                "text": text,
                "ts":   time.time(),
            })
            # Keep last 50 turns
            if len(self._data["chat_history"]) > 50:
                self._data["chat_history"] = self._data["chat_history"][-50:]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]


# Singleton — imported by other services
shared = SharedState()


# ── Dashboard service ─────────────────────────────────────────────────────────

class DashboardService:
    """
    Runs Flask in a daemon thread.
    Wires bus events into SharedState so the dashboard reflects live state.
    """

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        memory,           # MemoryService
        alert_svc,        # AlertService
        conscience,       # Conscience (optional)
        cmd_queue: queue.Queue,   # commands → MindService
        cfg,
        night_watch=None,   # NightWatchService (optional)
        experiments=None,   # ExperimentEngine (optional)
        summarizer=None,    # Summarizer (optional)
        learning=None,      # LearningService (optional)
    ):
        self._bus        = bus
        self._mm         = mode_manager
        self._memory     = memory
        self._alerts     = alert_svc
        self._conscience = conscience
        self._cmd_q      = cmd_queue
        self._cfg        = cfg
        self._nw         = night_watch
        self._exp        = experiments
        self._summ       = summarizer
        self._learn      = learning

        self._host = cfg.get("dashboard.host", "0.0.0.0")
        self._port = int(cfg.get("dashboard.port", 5000))

        # SSE subscribers
        self._sse_clients: list[queue.Queue] = []
        self._sse_lock = threading.Lock()

        # Wire bus → SharedState
        bus.subscribe(EventType.MODE_CHANGED,    self._on_mode)
        bus.subscribe(EventType.SCENE_ANALYZED,  self._on_scene)
        bus.subscribe(EventType.OBJECTS_DETECTED,self._on_objects)
        bus.subscribe(EventType.TTS_STARTED,     self._on_tts_start)
        bus.subscribe(EventType.TTS_FINISHED,    self._on_tts_finish)
        bus.subscribe(EventType.OBSTACLE_DETECTED,self._on_obstacle)
        bus.subscribe(EventType.OBSTACLE_CLEARED, self._on_obstacle_clear)
        bus.subscribe(EventType.HEARTBEAT,       self._on_heartbeat)
        bus.subscribe(EventType.PERSON_IDENTIFIED,self._on_person)
        bus.subscribe(EventType.ALERT_RAISED,    self._on_alert)
        bus.subscribe("*",        self._on_any)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        t = threading.Thread(target=self._run_flask, daemon=True, name="Dashboard")
        t.start()
        log.info(f"Dashboard: http://{self._host}:{self._port}")

    # ── Flask app ─────────────────────────────────────────────────────────────

    def _run_flask(self) -> None:
        try:
            from flask import Flask, request, jsonify, Response, render_template_string
        except ImportError:
            log.error("Dashboard: flask not installed — pip install flask")
            return

        app = Flask(__name__)
        app.logger.setLevel(logging.ERROR)

        # ── routes ────────────────────────────────────────────────────────────

        @app.route("/")
        def index():
            return render_template_string(_HTML_TEMPLATE)

        @app.route("/api/state")
        def api_state():
            state = shared.snapshot()
            if self._conscience:
                state["drives"] = self._conscience.to_dict()
            state["mode"] = self._mm.current.value
            return jsonify(state)

        @app.route("/api/persons")
        def api_persons():
            return jsonify(self._memory.all_persons())

        @app.route("/api/memory")
        def api_memory():
            return jsonify({
                "episodes": self._memory.recent_episodes(20),
                "facts":    self._memory.all_facts(),
            })

        @app.route("/api/alerts")
        def api_alerts():
            alerts = [
                {"level": a.level, "reason": a.reason, "ts": a.ts, **a.payload}
                for a in self._alerts.recent_alerts(30)
            ]
            return jsonify(alerts)

        @app.route("/api/night_log")
        def api_night_log():
            if self._nw is None:
                return jsonify([])
            return jsonify(self._nw.night_log())

        @app.route("/api/experiments")
        def api_experiments():
            if self._exp is None:
                return jsonify({"current": None, "history": []})
            current = self._exp.current.to_dict() if self._exp.current else None
            return jsonify({"current": current, "history": self._exp.history()})

        @app.route("/api/experiments/propose", methods=["POST"])
        def api_propose_experiment():
            if self._exp is None:
                return jsonify({"error": "no experiment engine"}), 503
            data = request.get_json(silent=True) or {}
            exp_id = self._exp.propose_experiment(data.get("id"))
            return jsonify({"started": exp_id})

        @app.route("/api/experiments/cancel", methods=["POST"])
        def api_cancel_experiment():
            if self._exp is None:
                return jsonify({"ok": False}), 503
            ok = self._exp.cancel_current()
            return jsonify({"ok": ok})

        @app.route("/api/summarize", methods=["POST"])
        def api_summarize():
            if self._summ is None:
                return jsonify({"error": "no summarizer"}), 503
            import threading
            threading.Thread(target=self._summ.summarize_now, daemon=True).start()
            return jsonify({"ok": True, "message": "summarization started"})

        @app.route("/api/learning")
        def api_learning():
            if self._learn is None:
                return jsonify({})
            return jsonify(self._learn.session_summary())

        @app.route("/api/command", methods=["POST"])
        def api_command():
            data = request.get_json(silent=True) or {}
            cmd  = (data.get("command") or "").strip()
            if not cmd:
                return jsonify({"error": "no command"}), 400
            self._cmd_q.put(cmd)
            shared.add_chat("user", cmd)
            log.info(f"Dashboard: injected command '{cmd}'")
            return jsonify({"ok": True})

        @app.route("/api/mode", methods=["POST"])
        def api_mode():
            data = request.get_json(silent=True) or {}
            mode_str = (data.get("mode") or "").strip()
            try:
                target = Mode(mode_str)
            except ValueError:
                return jsonify({"error": f"unknown mode '{mode_str}'"}), 400
            ok = self._mm.request_transition(target, reason="dashboard")
            return jsonify({"ok": ok, "current": self._mm.current.value})

        @app.route("/stream")
        def stream():
            q: queue.Queue = queue.Queue(maxsize=100)
            with self._sse_lock:
                self._sse_clients.append(q)

            def generate():
                try:
                    while True:
                        try:
                            data = q.get(timeout=20)
                            yield f"data: {data}\n\n"
                        except queue.Empty:
                            yield "data: {\"type\":\"ping\"}\n\n"
                except GeneratorExit:
                    pass
                finally:
                    with self._sse_lock:
                        self._sse_clients.remove(q)

            return Response(generate(), mimetype="text/event-stream",
                            headers={"X-Accel-Buffering": "no",
                                     "Cache-Control": "no-cache"})

        app.run(host=self._host, port=self._port, threaded=True, use_reloader=False)

    # ── SSE broadcast ─────────────────────────────────────────────────────────

    def _broadcast(self, data: dict) -> None:
        msg = json.dumps(data)
        with self._sse_lock:
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    # ── bus → SharedState handlers ────────────────────────────────────────────

    def _on_mode(self, ev) -> None:
        shared.update(mode=ev.get("to", ""))
        self._broadcast({"type": "mode_changed", "mode": ev.get("to")})

    def _on_scene(self, ev) -> None:
        shared.update(scene_description=ev.get("description", ""))

    def _on_objects(self, ev) -> None:
        shared.update(detected_objects=ev.get("objects", ""))

    def _on_tts_start(self, ev) -> None:
        shared.update(tts_speaking=True)
        text = ev.get("text", "")
        shared.add_chat("spooky", text)
        self._broadcast({"type": "tts", "text": text})

    def _on_tts_finish(self, ev) -> None:
        shared.update(tts_speaking=False)

    def _on_obstacle(self, ev) -> None:
        shared.update(obstacle_blocked=True, distance_cm=ev.get("distance_cm", 0))
        self._broadcast({"type": "obstacle", "blocked": True})

    def _on_obstacle_clear(self, ev) -> None:
        shared.update(obstacle_blocked=False, distance_cm=ev.get("distance_cm", 999))

    def _on_heartbeat(self, ev) -> None:
        shared.update(
            distance_cm  = ev.get("distance_cm", 999),
            cpu_temp_c   = ev.get("cpu_temp_c",  0),
            ram_free_mb  = ev.get("ram_free_mb", 8192),
        )

    def _on_person(self, ev) -> None:
        shared.update(last_person=ev.get("display_name"))
        self._broadcast({"type": "person", "name": ev.get("display_name")})

    def _on_alert(self, ev) -> None:
        self._broadcast({"type": "alert", "level": ev.get("level"), "reason": ev.get("reason")})

    def _on_any(self, ev) -> None:
        # Broadcast all events to SSE (debug/inspection)
        self._broadcast({"type": ev.type, **ev.payload})


# ── Minimal HTML template ─────────────────────────────────────────────────────

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="3">
<title>Spooky Dashboard</title>
<style>
  body { font-family: monospace; background: #0d0d0d; color: #c8ffc8; margin: 2rem; }
  h1   { color: #7fff7f; }
  h2   { color: #5fb; border-bottom: 1px solid #333; }
  .card { background: #111; border: 1px solid #2a2a2a; border-radius: 6px;
           padding: 1rem; margin: 0.8rem 0; }
  .badge { display:inline-block; padding:2px 8px; border-radius:4px;
            background:#1a3a1a; color:#7fff7f; margin:2px; }
  .alert { color: #f87; }
  input[type=text] { background:#111; border:1px solid #444; color:#cfc;
                     padding:4px 8px; width:60%; border-radius:4px; }
  button { background:#1a4a1a; color:#7fff7f; border:1px solid #444;
            padding:4px 10px; border-radius:4px; cursor:pointer; }
  table { border-collapse: collapse; width: 100%; }
  td,th { border: 1px solid #333; padding: 4px 8px; text-align: left; }
  th    { color: #5fb; }
  #log  { height: 200px; overflow-y: auto; border: 1px solid #333;
           padding: 0.5rem; font-size: 0.85em; }
</style>
</head>
<body>
<h1>🕷️ Spooky Dashboard</h1>

<div class="card">
  <h2>Stato</h2>
  <div id="state">Caricamento…</div>
</div>

<div class="card">
  <h2>Chat</h2>
  <div id="chat" style="height:150px;overflow-y:auto;"></div>
  <br>
  <input type="text" id="cmd" placeholder="Scrivi un comando…" onkeydown="if(event.key==='Enter')sendCmd()">
  <button onclick="sendCmd()">Invia</button>
</div>

<div class="card">
  <h2>Log eventi</h2>
  <div id="log"></div>
</div>

<div class="card">
  <h2>Modalità</h2>
  {% for m in ["companion_day","focus_assistant","idle_observer","night_watch"] %}
  <button onclick="setMode('{{m}}')">{{m}}</button>
  {% endfor %}
</div>

<script>
async function fetchState() {
  const r = await fetch('/api/state'); const d = await r.json();
  document.getElementById('state').innerHTML =
    `<span class="badge">mode: ${d.mode}</span>
     <span class="badge">mood: ${(d.drives||{}).mood||'?'}</span>
     <span class="badge">energy: ${((d.drives||{}).energy||0).toFixed(2)}</span>
     <span class="badge">fatigue: ${((d.drives||{}).interaction_fatigue||0).toFixed(2)}</span>
     <span class="badge">RAM: ${d.ram_free_mb}MB</span>
     <span class="badge">temp: ${d.cpu_temp_c}°C</span>
     <br><small>Scena: ${d.scene_description||'—'}</small>`;
}
fetchState(); setInterval(fetchState, 4000);

const es = new EventSource('/stream');
es.onmessage = e => {
  const d = JSON.parse(e.data);
  if(d.type==='ping') return;
  if(d.type==='tts' && d.text) {
    const c = document.getElementById('chat');
    c.innerHTML += `<div><b>🕷️</b> ${d.text}</div>`;
    c.scrollTop = c.scrollHeight;
  }
  const l = document.getElementById('log');
  l.innerHTML = `<div>${new Date().toLocaleTimeString()} [${d.type}] ${JSON.stringify(d)}</div>` + l.innerHTML;
};

async function sendCmd() {
  const el = document.getElementById('cmd');
  const cmd = el.value.trim(); if(!cmd) return;
  const c = document.getElementById('chat');
  c.innerHTML += `<div><b>👤</b> ${cmd}</div>`; c.scrollTop = c.scrollHeight;
  el.value = '';
  await fetch('/api/command', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:cmd})});
}

async function setMode(m) {
  await fetch('/api/mode', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:m})});
  fetchState();
}
</script>
</body>
</html>
"""
