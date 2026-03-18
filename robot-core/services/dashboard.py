"""
services/dashboard.py — Flask inspection dashboard.

Endpoints:
  GET  /                     → HTML dashboard (live)
  GET  /camera               → MJPEG live camera stream
  GET  /api/state            → JSON full robot state
  GET  /api/persons          → known persons
  GET  /api/memory           → episodes + facts
  GET  /api/alerts           → recent night watch alerts
  GET  /api/night_log        → night watch detailed log
  GET  /api/experiments      → experiment engine state
  GET  /api/learning         → learning session summary
  POST /api/command          → inject text command {"command": "..."}
  POST /api/mode             → switch mode {"mode": "night_watch"}
  POST /api/summarize        → trigger manual memory summarization
  GET  /stream               → SSE live event stream
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


# ── Shared state ──────────────────────────────────────────────────────────────

class SharedState:
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
            "chat_history":       [],
            "last_person":        None,
        }

    def update(self, **kwargs) -> None:
        with self._lock:
            self._data.update(kwargs)

    def add_chat(self, role: str, text: str) -> None:
        with self._lock:
            self._data["chat_history"].append({"role": role, "text": text, "ts": time.time()})
            if len(self._data["chat_history"]) > 50:
                self._data["chat_history"] = self._data["chat_history"][-50:]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            return self._data[key]


shared = SharedState()


# ── Dashboard service ─────────────────────────────────────────────────────────

class DashboardService:

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        memory,
        alert_svc,
        conscience,
        cmd_queue: queue.Queue,
        cfg,
        night_watch=None,
        experiments=None,
        summarizer=None,
        learning=None,
        vision=None,
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
        self._vision     = vision

        self._host = cfg.get("dashboard.host", "0.0.0.0")
        self._port = int(cfg.get("dashboard.port", 5000))

        self._sse_clients: list[queue.Queue] = []
        self._sse_lock = threading.Lock()

        bus.subscribe(EventType.MODE_CHANGED,     self._on_mode)
        bus.subscribe(EventType.SCENE_ANALYZED,   self._on_scene)
        bus.subscribe(EventType.OBJECTS_DETECTED, self._on_objects)
        bus.subscribe(EventType.TTS_STARTED,      self._on_tts_start)
        bus.subscribe(EventType.TTS_FINISHED,     self._on_tts_finish)
        bus.subscribe(EventType.OBSTACLE_DETECTED,self._on_obstacle)
        bus.subscribe(EventType.OBSTACLE_CLEARED, self._on_obstacle_clear)
        bus.subscribe(EventType.HEARTBEAT,        self._on_heartbeat)
        bus.subscribe(EventType.PERSON_IDENTIFIED,self._on_person)
        bus.subscribe(EventType.ALERT_RAISED,     self._on_alert)
        bus.subscribe("*",                        self._on_any)

    def start(self) -> None:
        t = threading.Thread(target=self._run_flask, daemon=True, name="Dashboard")
        t.start()

    # ── Flask ─────────────────────────────────────────────────────────────────

    def _run_flask(self) -> None:
        try:
            from flask import Flask, request, jsonify, Response, render_template_string
        except ImportError:
            log.error("Dashboard: flask not installed")
            return

        app = Flask(__name__)
        app.logger.setLevel(logging.ERROR)

        @app.route("/")
        def index():
            return render_template_string(_HTML_TEMPLATE)

        @app.route("/camera")
        def camera():
            if self._vision is None:
                return "no camera", 503
            return Response(self._mjpeg_stream(),
                            mimetype="multipart/x-mixed-replace; boundary=frame")

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
            return jsonify(self._nw.night_log() if self._nw else [])

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
            return jsonify({"ok": self._exp.cancel_current()})

        @app.route("/api/summarize", methods=["POST"])
        def api_summarize():
            if self._summ is None:
                return jsonify({"error": "no summarizer"}), 503
            threading.Thread(target=self._summ.summarize_now, daemon=True).start()
            return jsonify({"ok": True})

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
            q: queue.Queue = queue.Queue(maxsize=200)
            with self._sse_lock:
                self._sse_clients.append(q)
            def generate():
                try:
                    while True:
                        try:
                            data = q.get(timeout=20)
                            yield f"data: {data}\n\n"
                        except queue.Empty:
                            yield 'data: {"type":"ping"}\n\n'
                except GeneratorExit:
                    pass
                finally:
                    with self._sse_lock:
                        if q in self._sse_clients:
                            self._sse_clients.remove(q)
            return Response(generate(), mimetype="text/event-stream",
                            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

        # Find a free port via socket test (werkzeug calls sys.exit on bind failure)
        import socket as _socket
        port = self._port
        for attempt in range(5):
            with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("0.0.0.0", port))
                    break   # port is free
                except OSError:
                    log.warning(f"Dashboard: port {port} busy, trying {port+1}")
                    port += 1
        log.info(f"Dashboard: http://{self._host}:{port}")
        app.run(host=self._host, port=port, threaded=True, use_reloader=False)

    # ── MJPEG stream ──────────────────────────────────────────────────────────

    def _mjpeg_stream(self):
        try:
            import cv2
        except ImportError:
            return
        while True:
            frame = self._vision.get_frame()
            if frame is None:
                time.sleep(0.1)
                continue
            # Convert RGB → BGR for cv2
            bgr = frame[:, :, ::-1]
            ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                time.sleep(0.05)
                continue
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" +
                   buf.tobytes() + b"\r\n")
            time.sleep(0.05)   # ~20 fps

    # ── SSE broadcast ─────────────────────────────────────────────────────────

    def _broadcast(self, data: dict) -> None:
        msg = json.dumps(data)
        with self._sse_lock:
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    # ── bus handlers ──────────────────────────────────────────────────────────

    def _on_mode(self, ev) -> None:
        shared.update(mode=ev.get("to", ""))
        self._broadcast({"type": "mode_changed", "mode": ev.get("to")})

    def _on_scene(self, ev) -> None:
        shared.update(scene_description=ev.get("description", ""))
        self._broadcast({"type": "scene", "description": ev.get("description", "")})

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
        self._broadcast({"type": "obstacle", "blocked": True, "cm": ev.get("distance_cm", 0)})

    def _on_obstacle_clear(self, ev) -> None:
        shared.update(obstacle_blocked=False, distance_cm=ev.get("distance_cm", 999))
        self._broadcast({"type": "obstacle", "blocked": False})

    def _on_heartbeat(self, ev) -> None:
        shared.update(
            distance_cm = ev.get("distance_cm", 999),
            cpu_temp_c  = ev.get("cpu_temp_c",  0),
            ram_free_mb = ev.get("ram_free_mb", 8192),
        )
        self._broadcast({
            "type":        "heartbeat",
            "distance_cm": ev.get("distance_cm", 999),
            "cpu_temp_c":  ev.get("cpu_temp_c", 0),
            "ram_free_mb": ev.get("ram_free_mb", 8192),
        })

    def _on_person(self, ev) -> None:
        shared.update(last_person=ev.get("display_name"))
        self._broadcast({"type": "person", "name": ev.get("display_name"), "confidence": ev.get("confidence", 0)})

    def _on_alert(self, ev) -> None:
        self._broadcast({"type": "alert", "level": ev.get("level"), "reason": ev.get("reason")})

    def _on_any(self, ev) -> None:
        self._broadcast({"type": ev.type, **ev.payload})


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spooky</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:monospace;background:#0a0a0a;color:#c8ffc8;font-size:14px}
header{background:#0d1a0d;border-bottom:1px solid #1e3a1e;padding:.6rem 1.2rem;
       display:flex;align-items:center;gap:1rem}
header h1{font-size:1.2rem;color:#7fff7f}
#modebadge{padding:3px 10px;border-radius:4px;background:#1a3a1a;
           color:#7fff7f;font-weight:bold;font-size:.85rem}
#speaking{display:none;color:#fa0;font-size:.8rem}
.grid{display:grid;grid-template-columns:1fr 340px;gap:.8rem;padding:.8rem;height:calc(100vh - 50px)}
.left{display:flex;flex-direction:column;gap:.8rem;overflow-y:auto}
.right{display:flex;flex-direction:column;gap:.8rem;overflow-y:auto}
.card{background:#0f1a0f;border:1px solid #1e3a1e;border-radius:6px;padding:.8rem}
.card h2{color:#5fb;font-size:.85rem;margin-bottom:.5rem;
         border-bottom:1px solid #1e3a1e;padding-bottom:.3rem}
/* Camera */
#cam{width:100%;border-radius:4px;background:#000;display:block;aspect-ratio:4/3;
     object-fit:contain}
/* Metrics grid */
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:.4rem;margin-top:.3rem}
.metric{background:#0a120a;border:1px solid #1e3a1e;border-radius:4px;
        padding:.4rem;text-align:center}
.metric .val{font-size:1.2rem;font-weight:bold;color:#7fff7f}
.metric .lbl{font-size:.65rem;color:#5fb;margin-top:2px}
/* Drives */
.drives{display:grid;grid-template-columns:1fr 1fr;gap:.4rem}
.drive{background:#0a120a;border:1px solid #1e3a1e;border-radius:4px;padding:.35rem .5rem}
.drive .name{font-size:.65rem;color:#5fb;margin-bottom:2px}
.bar-track{background:#111;border-radius:3px;height:6px;overflow:hidden}
.bar-fill{height:100%;background:#3fa;border-radius:3px;transition:width .5s}
.bar-fill.danger{background:#f55}
.bar-fill.warn{background:#fa0}
/* Chat */
#chat{height:180px;overflow-y:auto;padding:.3rem;background:#050e05;
      border-radius:4px;border:1px solid #1e3a1e;margin-bottom:.5rem}
.msg{margin:.2rem 0;line-height:1.4}
.msg.spooky span{color:#7fff7f}
.msg.user  span{color:#5bf}
.msg.system span{color:#888;font-style:italic}
.chat-input{display:flex;gap:.4rem}
.chat-input input{flex:1;background:#050e05;border:1px solid #2a4a2a;
                  color:#cfc;padding:.35rem .5rem;border-radius:4px;outline:none}
.chat-input input:focus{border-color:#5fb}
.chat-input button{background:#1a3a1a;color:#7fff7f;border:1px solid #2a4a2a;
                   padding:.35rem .7rem;border-radius:4px;cursor:pointer}
/* Mode buttons */
.modes{display:grid;grid-template-columns:1fr 1fr;gap:.4rem}
.modes button{background:#0a1a0a;color:#7fff7f;border:1px solid #2a4a2a;
              padding:.4rem;border-radius:4px;cursor:pointer;font-family:monospace;
              font-size:.75rem}
.modes button:hover{background:#1a3a1a}
.modes button.active{background:#1a4a1a;border-color:#5fb;font-weight:bold}
/* Log */
#log{height:140px;overflow-y:auto;font-size:.75rem;color:#888;
     background:#050e05;border-radius:4px;padding:.3rem;border:1px solid #1e3a1e}
.log-entry{padding:1px 0;border-bottom:1px solid #0f1a0f}
.log-entry.alert{color:#f87}
.log-entry.person{color:#7df}
/* Facts table */
#facts{width:100%;border-collapse:collapse;font-size:.75rem}
#facts td,#facts th{border:1px solid #1e3a1e;padding:2px 6px}
#facts th{color:#5fb;background:#0a120a}
/* Person */
#person-name{font-size:1rem;color:#7fff7f;margin:.3rem 0}
#person-conf{font-size:.75rem;color:#888}
/* Obstacle */
#obstacle-badge{display:none;background:#4a0a0a;color:#f87;
                padding:3px 8px;border-radius:4px;font-size:.8rem}
/* Responsive */
@media(max-width:800px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>

<header>
  <h1>🕷️ Spooky</h1>
  <span id="modebadge">—</span>
  <span id="speaking">🔊 parlando…</span>
  <span id="obstacle-badge">⚠️ ostacolo</span>
  <span style="margin-left:auto;color:#888;font-size:.75rem" id="clock"></span>
</header>

<div class="grid">

  <!-- LEFT column -->
  <div class="left">

    <!-- Camera -->
    <div class="card">
      <h2>Camera</h2>
      <img id="cam" src="/camera" alt="camera" onerror="this.style.opacity=0.3">
      <div style="margin-top:.4rem;font-size:.75rem;color:#888" id="scene-desc">—</div>
    </div>

    <!-- Metrics -->
    <div class="card">
      <h2>Sensori &amp; Sistema</h2>
      <div class="metrics">
        <div class="metric"><div class="val" id="m-dist">—</div><div class="lbl">dist cm</div></div>
        <div class="metric"><div class="val" id="m-temp">—</div><div class="lbl">CPU °C</div></div>
        <div class="metric"><div class="val" id="m-ram">—</div><div class="lbl">RAM MB</div></div>
      </div>
    </div>

    <!-- Drives -->
    <div class="card">
      <h2>Drive interni</h2>
      <div class="drives" id="drives-grid">
        <!-- filled by JS -->
      </div>
    </div>

    <!-- Last person -->
    <div class="card">
      <h2>Persona rilevata</h2>
      <div id="person-name">nessuno</div>
      <div id="person-conf"></div>
    </div>

    <!-- Memory facts -->
    <div class="card">
      <h2>Memoria semantica</h2>
      <table id="facts"><thead><tr><th>chiave</th><th>valore</th><th>conf</th></tr></thead>
      <tbody id="facts-body"></tbody></table>
    </div>

  </div>

  <!-- RIGHT column -->
  <div class="right">

    <!-- Modalità -->
    <div class="card">
      <h2>Modalità</h2>
      <div class="modes">
        <button onclick="setMode('companion_day')"   id="btn-companion_day">companion_day</button>
        <button onclick="setMode('focus_assistant')" id="btn-focus_assistant">focus_assistant</button>
        <button onclick="setMode('idle_observer')"   id="btn-idle_observer">idle_observer</button>
        <button onclick="setMode('night_watch')"     id="btn-night_watch">🌙 night_watch</button>
      </div>
    </div>

    <!-- Chat -->
    <div class="card" style="flex:1">
      <h2>Chat</h2>
      <div id="chat"></div>
      <div class="chat-input">
        <input id="cmd" placeholder="Scrivi un comando…"
               onkeydown="if(event.key==='Enter')sendCmd()">
        <button onclick="sendCmd()">▶</button>
      </div>
    </div>

    <!-- Azioni rapide -->
    <div class="card">
      <h2>Azioni rapide</h2>
      <div style="display:flex;gap:.4rem;flex-wrap:wrap">
        <button class="modes" style="width:auto;padding:.3rem .6rem"
                onclick="sendRaw('cosa vedi?')">👁 Scena</button>
        <button class="modes" style="width:auto;padding:.3rem .6rem"
                onclick="sendRaw('cosa ricordi?')">🧠 Memoria</button>
        <button class="modes" style="width:auto;padding:.3rem .6rem"
                onclick="summarize()">💾 Riassumi</button>
      </div>
    </div>

    <!-- Log eventi -->
    <div class="card">
      <h2>Log eventi <span style="color:#444;font-size:.7rem" id="log-count"></span></h2>
      <div id="log"></div>
    </div>

  </div>
</div>

<script>
/* ── clock ── */
setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleTimeString('it')},1000);

/* ── helpers ── */
function badge(id,text){const e=document.getElementById(id);if(e)e.textContent=text;}
function val(id,text){const e=document.getElementById(id);if(e)e.textContent=text;}
function pct(val){return Math.round((val||0)*100);}

/* ── drives ── */
const DRIVES=[
  {k:'energy',       lbl:'Energia'},
  {k:'social_drive', lbl:'Social'},
  {k:'curiosity',    lbl:'Curiosità'},
  {k:'attention',    lbl:'Attenzione'},
  {k:'interaction_fatigue', lbl:'Fatica', danger:true},
];
function renderDrives(d){
  const g=document.getElementById('drives-grid');
  g.innerHTML=DRIVES.map(({k,lbl,danger})=>{
    const v=d[k]||0;
    const pct=Math.round(v*100);
    const cls=danger?(v>0.7?'danger':v>0.4?'warn':''):(v<0.2?'warn':'');
    return `<div class="drive"><div class="name">${lbl} <b>${pct}%</b></div>
      <div class="bar-track"><div class="bar-fill ${cls}" style="width:${pct}%"></div></div>
    </div>`;
  }).join('');
}

/* ── mode buttons ── */
let _mode='';
function updateModeButtons(m){
  if(m===_mode)return; _mode=m;
  document.querySelectorAll('.modes button').forEach(b=>b.classList.remove('active'));
  const b=document.getElementById('btn-'+m);
  if(b)b.classList.add('active');
  document.getElementById('modebadge').textContent=m;
}

/* ── state polling ── */
async function fetchState(){
  try{
    const r=await fetch('/api/state'); const d=await r.json();
    updateModeButtons(d.mode||'');
    const dr=d.drives||{};
    renderDrives(dr);
    val('m-dist',(d.distance_cm>=990?'—':(d.distance_cm||0).toFixed(0)));
    val('m-temp',(d.cpu_temp_c||0).toFixed(1));
    val('m-ram', d.ram_free_mb||0);
    document.getElementById('scene-desc').textContent=d.scene_description||'—';
    const sp=document.getElementById('speaking');
    sp.style.display=d.tts_speaking?'inline':'none';
    const ob=document.getElementById('obstacle-badge');
    ob.style.display=d.obstacle_blocked?'inline':'none';
    if(d.last_person){
      document.getElementById('person-name').textContent=d.last_person;
    }
  }catch(e){}
}
fetchState(); setInterval(fetchState,4000);

/* ── memory facts polling ── */
async function fetchFacts(){
  try{
    const r=await fetch('/api/memory'); const d=await r.json();
    const tb=document.getElementById('facts-body');
    tb.innerHTML=(d.facts||[]).slice(0,10).map(f=>
      `<tr><td>${f.key}</td><td>${f.value}</td><td>${(f.confidence*100).toFixed(0)}%</td></tr>`
    ).join('');
  }catch(e){}
}
fetchFacts(); setInterval(fetchFacts,8000);

/* ── SSE ── */
let logCount=0;
const es=new EventSource('/stream');
es.onmessage=e=>{
  try{
    const d=JSON.parse(e.data);
    if(d.type==='ping') return;

    if(d.type==='mode_changed') updateModeButtons(d.mode||'');
    if(d.type==='tts' && d.text) addChat('spooky',d.text);
    if(d.type==='scene' && d.description)
      document.getElementById('scene-desc').textContent=d.description;
    if(d.type==='person' && d.name){
      document.getElementById('person-name').textContent=d.name;
      document.getElementById('person-conf').textContent=
        d.confidence?'conf: '+(d.confidence*100).toFixed(0)+'%':'';
    }
    if(d.type==='obstacle'){
      document.getElementById('obstacle-badge').style.display=d.blocked?'inline':'none';
    }
    if(d.type==='heartbeat'){
      val('m-dist',d.distance_cm>=990?'—':d.distance_cm.toFixed(0));
      val('m-temp',d.cpu_temp_c.toFixed(1));
      val('m-ram', d.ram_free_mb);
    }

    // Log
    const isAlert=d.type==='alert'||d.type==='obstacle';
    const isPerson=d.type==='person';
    const skip=['ping','heartbeat'].includes(d.type);
    if(!skip){
      const l=document.getElementById('log');
      const row=document.createElement('div');
      row.className='log-entry'+(isAlert?' alert':isPerson?' person':'');
      const ts=new Date().toLocaleTimeString('it');
      row.textContent=`${ts} [${d.type}] `+JSON.stringify(d).slice(0,80);
      l.prepend(row);
      if(l.children.length>100) l.removeChild(l.lastChild);
      logCount++;
      document.getElementById('log-count').textContent=`(${logCount})`;
    }
  }catch(err){}
};
es.onerror=()=>{ console.warn('SSE disconnected, retry...'); };

/* ── chat ── */
function addChat(role,text){
  const c=document.getElementById('chat');
  const d=document.createElement('div');
  d.className='msg '+role;
  const icon=role==='spooky'?'🕷️':role==='user'?'👤':'⚙️';
  d.innerHTML=`<span>${icon} ${text}</span>`;
  c.appendChild(d);
  c.scrollTop=c.scrollHeight;
}

async function sendCmd(){
  const el=document.getElementById('cmd');
  const cmd=el.value.trim(); if(!cmd) return;
  addChat('user',cmd); el.value='';
  await fetch('/api/command',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({command:cmd})});
}

async function sendRaw(cmd){
  addChat('user',cmd);
  await fetch('/api/command',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({command:cmd})});
}

async function setMode(m){
  await fetch('/api/mode',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({mode:m})});
  await fetchState();
}

async function summarize(){
  await fetch('/api/summarize',{method:'POST'});
  addChat('system','Riassunto memoria avviato…');
}
</script>
</body>
</html>
"""
