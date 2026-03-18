"""
services/vision.py — Camera, face detection, face recognition, scene analysis.

Architecture:
  CameraBackend    — abstracts picamera2 / OpenCV webcam / simulation
  FaceDatabase     — stores embeddings + metadata per person (local, no cloud)
  FaceDetector     — OpenCV haar cascade (zero extra downloads)
  FaceRecognizer   — OpenCV LBPH (lightweight, ~1 MB RAM, works on RPi 5)
  VisionService    — orchestrates camera loop, face events, scene analysis

Published events:
  PERSON_DETECTED    — face found in frame (may be unknown)
  PERSON_IDENTIFIED  — known person, confidence ≥ high threshold
  PERSON_UNKNOWN     — face found but not recognised or confidence < mid
  PERSON_LOST        — no face for N consecutive frames
  SCENE_ANALYZED     — ollama vision model description
  OBJECTS_DETECTED   — ollama object list

Upgrade path to face_recognition/insightface: swap FaceRecognizer subclass only.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from core.bus import EventBus, EventType
from core.safety import SafetyMonitor

log = logging.getLogger(__name__)

# ── Global Ollama lock (shared with MindService) ─────────────────────────────
# Imported lazily to avoid circular imports; set by main.py via set_ollama_lock()
_OLLAMA_LOCK: Optional[threading.Lock] = None

def set_ollama_lock(lock: threading.Lock) -> None:
    global _OLLAMA_LOCK
    _OLLAMA_LOCK = lock


# ══════════════════════════════════════════════════════════════════════════════
# Camera backend
# ══════════════════════════════════════════════════════════════════════════════

class CameraBackend:
    """
    Abstracts frame capture.
    Returns RGB uint8 frames always (not BGR) to avoid constant conversion.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 30):
        self._w   = width
        self._h   = height
        self._fps = fps
        self._cam = None
        self._backend = "none"

    def open(self) -> bool:
        # 1. Try picamera2 (RPi)
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            cam.configure(cam.create_preview_configuration(
                main={"format": "RGB888", "size": (self._w, self._h)}
            ))
            cam.start()
            self._cam     = cam
            self._backend = "picamera2"
            log.info(f"Camera: picamera2 {self._w}×{self._h}")
            return True
        except Exception as e:
            log.debug(f"picamera2 unavailable: {e}")

        # 2. Try OpenCV webcam
        try:
            cap = cv2.VideoCapture(0)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self._w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._h)
                cap.set(cv2.CAP_PROP_FPS,          self._fps)
                self._cam     = cap
                self._backend = "opencv"
                log.info(f"Camera: OpenCV webcam {self._w}×{self._h}")
                return True
        except Exception as e:
            log.debug(f"OpenCV webcam unavailable: {e}")

        # 3. Simulation
        log.warning("Camera: no hardware — simulation mode (blank frames)")
        self._backend = "sim"
        return False

    def read_rgb(self) -> Optional[np.ndarray]:
        """Returns an RGB frame or None on failure."""
        if self._backend == "picamera2":
            try:
                return self._cam.capture_array()
            except Exception as e:
                log.error(f"Camera read error: {e}")
                return None

        if self._backend == "opencv":
            ok, bgr = self._cam.read()
            if ok:
                return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            return None

        # sim: white noise frame
        return np.zeros((self._h, self._w, 3), dtype=np.uint8)

    def close(self) -> None:
        try:
            if self._backend == "picamera2":
                self._cam.stop()
                self._cam.close()
            elif self._backend == "opencv":
                self._cam.release()
        except Exception:
            pass

    @property
    def backend(self) -> str:
        return self._backend


# ══════════════════════════════════════════════════════════════════════════════
# Face database
# ══════════════════════════════════════════════════════════════════════════════

class FaceDatabase:
    """
    Local face storage. Layout:
        data/faces/
            index.json          {person_id: display_name}
            <person_id>/
                samples/        raw face crops (JPEG)
                embedding.npy   mean LBPH histogram (or future embedding)

    Thread-safe for concurrent reads; single-writer.
    """

    def __init__(self, db_path: Path):
        self._root   = Path(db_path)
        self._root.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, str] = {}   # person_id → display_name
        self._lock   = threading.RLock()
        self._load_index()

    def _load_index(self) -> None:
        idx = self._root / "index.json"
        if idx.exists():
            try:
                with open(idx) as f:
                    self._index = json.load(f)
                log.info(f"FaceDB: {len(self._index)} persons loaded")
            except Exception as e:
                log.error(f"FaceDB: index load error — {e}")

    def _save_index(self) -> None:
        with open(self._root / "index.json", "w") as f:
            json.dump(self._index, f, indent=2)

    def all_persons(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._index)

    def display_name(self, person_id: str) -> Optional[str]:
        return self._index.get(person_id)

    def enroll_person(
        self,
        person_id: str,
        display_name: str,
        face_crops: List[np.ndarray],
        recognizer: "FaceRecognizer",
    ) -> bool:
        """Save face crops and compute mean embedding."""
        if not face_crops:
            log.error("FaceDB.enroll: no face crops provided")
            return False

        person_dir   = self._root / person_id
        samples_dir  = person_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            # Save raw crops
            for i, crop in enumerate(face_crops):
                gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY) if crop.ndim == 3 else crop
                cv2.imwrite(str(samples_dir / f"{i:04d}.jpg"), gray)

            # Ask recognizer to compute + save embedding
            recognizer.enroll(person_id, face_crops, person_dir)

            self._index[person_id] = display_name
            self._save_index()

        log.info(f"FaceDB: enrolled '{display_name}' ({person_id}) with {len(face_crops)} samples")
        return True

    def delete_person(self, person_id: str) -> None:
        import shutil
        with self._lock:
            if person_id in self._index:
                del self._index[person_id]
                self._save_index()
            d = self._root / person_id
            if d.exists():
                shutil.rmtree(d)
        log.info(f"FaceDB: deleted '{person_id}'")

    def load_all_embeddings(
        self, recognizer: "FaceRecognizer"
    ) -> int:
        """Load all enrolled embeddings into recognizer. Returns count."""
        n = 0
        with self._lock:
            for pid in list(self._index.keys()):
                person_dir = self._root / pid
                if recognizer.load_embedding(pid, person_dir):
                    n += 1
        return n


# ══════════════════════════════════════════════════════════════════════════════
# Face recognizer (OpenCV LBPH — lightweight, ~1 MB RAM)
# ══════════════════════════════════════════════════════════════════════════════

class FaceRecognizer:
    """
    OpenCV LBPH face recogniser.

    Stores a trained LBPH model per person plus a label→person_id map.
    On predict(), returns (person_id, confidence) where confidence is in [0,1]
    (converted from LBPH distance — lower distance = better match).

    Upgrade path: replace with face_recognition embeddings by subclassing and
    overriding enroll() / predict() / load_embedding().
    """

    # LBPH distance below which we consider it a match
    _DIST_MAX = 120.0   # tunable; lower = stricter

    def __init__(self):
        self._recognizer = cv2.face.LBPHFaceRecognizer_create(
            radius=1, neighbors=8, grid_x=8, grid_y=8
        )
        self._label_map:  Dict[int, str] = {}   # label_int → person_id
        self._id_to_label: Dict[str, int] = {}
        self._trained = False
        self._lock    = threading.RLock()

    def enroll(
        self,
        person_id: str,
        face_crops_rgb: List[np.ndarray],
        save_dir: Path,
    ) -> None:
        """Compute and persist LBPH embedding for a person."""
        grays = [
            cv2.cvtColor(c, cv2.COLOR_RGB2GRAY) if c.ndim == 3 else c
            for c in face_crops_rgb
        ]
        # Normalise size
        target = (100, 100)
        grays = [cv2.resize(g, target) for g in grays]

        with self._lock:
            label = self._id_to_label.get(person_id)
            if label is None:
                label = len(self._label_map)
                self._label_map[label]     = person_id
                self._id_to_label[person_id] = label

            labels = [label] * len(grays)

            if self._trained:
                self._recognizer.update(grays, np.array(labels))
            else:
                self._recognizer.train(grays, np.array(labels))
                self._trained = True

            # Persist
            model_path = save_dir / "lbph_model.xml"
            self._recognizer.save(str(model_path))

            # Save label map
            with open(save_dir / "label.json", "w") as f:
                json.dump({"person_id": person_id, "label": label}, f)

    def load_embedding(self, person_id: str, person_dir: Path) -> bool:
        model_path = person_dir / "lbph_model.xml"
        label_path = person_dir / "label.json"
        if not model_path.exists() or not label_path.exists():
            return False
        try:
            with self._lock:
                with open(label_path) as f:
                    meta = json.load(f)
                label = meta["label"]
                self._label_map[label]        = person_id
                self._id_to_label[person_id]  = label
                # LBPH doesn't support incremental loading — retrain from samples
                samples_dir = person_dir / "samples"
                grays, lbls = [], []
                for p in sorted(samples_dir.glob("*.jpg")):
                    g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
                    if g is not None:
                        grays.append(cv2.resize(g, (100, 100)))
                        lbls.append(label)
                if not grays:
                    return False
                if self._trained:
                    self._recognizer.update(grays, np.array(lbls))
                else:
                    self._recognizer.train(grays, np.array(lbls))
                    self._trained = True
            return True
        except Exception as e:
            log.error(f"FaceRecognizer.load_embedding ({person_id}): {e}")
            return False

    def predict(self, face_gray: np.ndarray) -> Tuple[Optional[str], float]:
        """
        Returns (person_id, confidence) or (None, 0.0) if no match.
        confidence is in [0, 1] — higher = more certain.
        """
        if not self._trained:
            return None, 0.0
        face_r = cv2.resize(face_gray, (100, 100))
        with self._lock:
            try:
                label, dist = self._recognizer.predict(face_r)
                person_id   = self._label_map.get(int(label))
                # Convert distance to 0-1 confidence (inverse, clamped)
                conf = max(0.0, 1.0 - dist / self._DIST_MAX)
                return person_id, conf
            except Exception as e:
                log.debug(f"FaceRecognizer.predict error: {e}")
                return None, 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Face detector (OpenCV haar cascade)
# ══════════════════════════════════════════════════════════════════════════════

class FaceDetector:
    """
    OpenCV frontalface haar cascade. Fast, zero extra downloads.
    Returns list of (x, y, w, h) bounding boxes (in RGB frame coordinates).
    """

    def __init__(self, scale_factor: float = 1.3, min_neighbours: int = 5):
        data_dir = cv2.data.haarcascades
        path     = os.path.join(data_dir, "haarcascade_frontalface_default.xml")
        self._clf = cv2.CascadeClassifier(path)
        self._sf  = scale_factor
        self._mn  = min_neighbours

    def detect(self, frame_rgb: np.ndarray) -> List[Tuple[int,int,int,int]]:
        gray  = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        faces = self._clf.detectMultiScale(gray, self._sf, self._mn, minSize=(60, 60))
        if len(faces) == 0:
            return []
        return [(int(x), int(y), int(w), int(h)) for x, y, w, h in faces]


# ══════════════════════════════════════════════════════════════════════════════
# VisionService
# ══════════════════════════════════════════════════════════════════════════════

class VisionService:
    """
    Orchestrates:
      - continuous camera capture
      - face detection + recognition (every FACE_INTERVAL seconds)
      - ollama scene analysis (every SCENE_INTERVAL seconds)
      - ollama object detection (every OBJECT_INTERVAL seconds)

    All ollama calls go through the shared _OLLAMA_LOCK.
    """

    def __init__(
        self,
        bus: EventBus,
        safety: SafetyMonitor,
        face_db: FaceDatabase,
        cfg,   # ConfigNode
    ):
        self._bus     = bus
        self._safety  = safety
        self._db      = face_db
        self._cfg     = cfg

        # Thresholds
        self._conf_high = cfg.get("face.confidence_high", 0.80)
        self._conf_mid  = cfg.get("face.confidence_mid",  0.55)

        # Camera
        cam_cfg = cfg.get("camera", {})
        self._camera = CameraBackend(
            width  = cam_cfg.get("width",  640),
            height = cam_cfg.get("height", 480),
            fps    = cam_cfg.get("fps",    30),
        )

        # Detector + recognizer
        self._detector   = FaceDetector()
        self._recognizer = FaceRecognizer()

        # Intervals
        self._face_interval   = cfg.get("camera.face_detect_interval_s", 0.25)
        self._scene_interval  = cfg.get("vision_llm.scene_interval_s",   120)
        self._object_interval = cfg.get("vision_llm.object_interval_s",  150)

        # State
        self._active          = False
        self._last_frame_rgb: Optional[np.ndarray] = None
        self._frame_lock      = threading.Lock()
        self._consecutive_no_face = 0
        self._face_loss_threshold = 8   # frames before PERSON_LOST

        # Ollama vision model
        self._vision_model: Optional[str] = None

        # Thread handles
        self._capture_thread: Optional[threading.Thread] = None
        self._face_thread:    Optional[threading.Thread] = None
        self._scene_thread:   Optional[threading.Thread] = None

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        ok = self._camera.open()
        if not ok:
            log.warning("VisionService: camera unavailable — face/scene events won't fire")

        # Load known faces into recognizer
        n = self._db.load_all_embeddings(self._recognizer)
        log.info(f"VisionService: {n} persons loaded from face DB")

        # Resolve vision LLM model
        self._vision_model = self._resolve_vision_model()

        self._active = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="Vision-Capture"
        )
        self._face_thread = threading.Thread(
            target=self._face_loop, daemon=True, name="Vision-Face"
        )
        self._scene_thread = threading.Thread(
            target=self._scene_loop, daemon=True, name="Vision-Scene"
        )
        self._capture_thread.start()
        self._face_thread.start()
        self._scene_thread.start()
        log.info(f"VisionService started (camera={self._camera.backend})")

    def stop(self) -> None:
        self._active = False
        self._camera.close()
        log.info("VisionService stopped")

    # ── public: enrollment ────────────────────────────────────────────────────

    def enroll_from_camera(
        self,
        person_id: str,
        display_name: str,
        n_frames: int = 15,
        timeout_s: float = 20.0,
    ) -> bool:
        """
        Interactively enroll a person: capture N face crops from live camera.
        Call from CLI / dashboard thread (blocks until done or timeout).
        """
        log.info(f"Enrolling '{display_name}' — look at the camera ({n_frames} samples needed)")
        crops: List[np.ndarray] = []
        deadline = time.time() + timeout_s

        while len(crops) < n_frames and time.time() < deadline:
            frame = self._get_frame()
            if frame is None:
                time.sleep(0.1); continue
            faces = self._detector.detect(frame)
            if faces:
                x, y, w, h = faces[0]
                crop = frame[y:y+h, x:x+w]
                if crop.size > 0:
                    crops.append(crop)
                    log.debug(f"Enroll: {len(crops)}/{n_frames}")
            time.sleep(0.15)

        if len(crops) < n_frames // 2:
            log.error(f"Enrollment failed: only {len(crops)} samples captured")
            return False

        return self._db.enroll_person(person_id, display_name, crops, self._recognizer)

    # ── capture loop (fast — just keeps _last_frame_rgb fresh) ───────────────

    def _capture_loop(self) -> None:
        while self._active:
            frame = self._camera.read_rgb()
            if frame is not None:
                with self._frame_lock:
                    self._last_frame_rgb = frame
            time.sleep(0.033)   # ~30 fps

    def get_frame(self) -> Optional[np.ndarray]:
        """Public: return a copy of the latest RGB frame (or None if no camera)."""
        with self._frame_lock:
            f = self._last_frame_rgb
            return f.copy() if f is not None else None

    def _get_frame(self) -> Optional[np.ndarray]:
        return self.get_frame()

    # ── face detection + recognition loop ────────────────────────────────────

    def _face_loop(self) -> None:
        while self._active:
            frame = self._get_frame()
            if frame is not None:
                self._process_faces(frame)
            time.sleep(self._face_interval)

    def _process_faces(self, frame: np.ndarray) -> None:
        faces = self._detector.detect(frame)

        if not faces:
            self._consecutive_no_face += 1
            if self._consecutive_no_face == self._face_loss_threshold:
                self._bus.publish(EventType.PERSON_LOST, {}, source="VisionService")
            return

        self._consecutive_no_face = 0
        x, y, w, h = faces[0]   # process largest/first face only for now
        crop  = frame[y:y+h, x:x+w]
        gray  = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

        person_id, conf = self._recognizer.predict(gray)

        # Normalised face centre offset (-1..1) for head tracking
        frame_h, frame_w = frame.shape[:2]
        cx = (x + w / 2) / frame_w * 2 - 1   # -1=left, +1=right
        cy = (y + h / 2) / frame_h * 2 - 1   # -1=top,  +1=bottom

        base_payload = {
            "bbox":       [x, y, w, h],
            "center_x":   round(cx, 3),
            "center_y":   round(cy, 3),
            "confidence": round(conf, 3),
        }

        self._bus.publish(
            EventType.PERSON_DETECTED,
            {**base_payload, "person_id": person_id},
            source="VisionService",
        )

        if conf >= self._conf_high and person_id:
            display = self._db.display_name(person_id) or person_id
            self._bus.publish(
                EventType.PERSON_IDENTIFIED,
                {**base_payload, "person_id": person_id, "display_name": display},
                source="VisionService",
            )
        else:
            self._bus.publish(
                EventType.PERSON_UNKNOWN,
                base_payload,
                source="VisionService",
            )

    # ── scene + object analysis (ollama) ─────────────────────────────────────

    def _scene_loop(self) -> None:
        last_scene  = 0.0
        last_object = 0.0
        while self._active:
            now = time.time()
            if self._vision_model:
                if now - last_scene >= self._scene_interval:
                    self._analyze_scene()
                    last_scene = time.time()
                if now - last_object >= self._object_interval:
                    self._analyze_objects()
                    last_object = time.time()
            time.sleep(10)   # check every 10s

    def _analyze_scene(self) -> None:
        frame = self._get_frame()
        if frame is None:
            return
        ram = self._safety.get_ram_free_mb() if hasattr(self._safety, 'get_ram_free_mb') else 9999
        min_ram = self._cfg.get("vision_llm.min_ram_mb_to_infer", 1200)
        if ram < min_ram:
            log.warning(f"VisionService: scene skip — RAM {ram}MB < {min_ram}MB")
            return

        if _OLLAMA_LOCK and not _OLLAMA_LOCK.acquire(blocking=False):
            log.debug("VisionService: scene skip — ollama busy")
            return
        try:
            import ollama
            img_b64 = self._frame_to_b64(frame)
            keep_alive = self._cfg.get("vision_llm.keep_alive_s", 0)
            max_tok    = self._cfg.get("vision_llm.max_tokens", 90)
            resp = ollama.chat(
                model=self._vision_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "Sei Spooky, un robot ragno sulla scrivania. "
                        "Descrivi in italiano cosa vedi: oggetti, persone, ambiente. "
                        "2 frasi brevi, prima persona. Inizia con 'Vedo'"
                    ),
                    "images": [img_b64],
                }],
                options={"temperature": 0.3, "num_predict": max_tok},
                keep_alive=keep_alive,
            )
            desc = self._extract_text(resp)
            log.info(f"👁️  {desc}")
            self._bus.publish(EventType.SCENE_ANALYZED, {"description": desc}, source="VisionService")
        except Exception as e:
            log.warning(f"VisionService scene analysis: {e}")
        finally:
            if _OLLAMA_LOCK:
                _OLLAMA_LOCK.release()

    def _analyze_objects(self) -> None:
        frame = self._get_frame()
        if frame is None:
            return
        if _OLLAMA_LOCK and not _OLLAMA_LOCK.acquire(blocking=False):
            log.debug("VisionService: object skip — ollama busy")
            return
        try:
            import ollama
            img_b64  = self._frame_to_b64(frame)
            keep_alive = self._cfg.get("vision_llm.keep_alive_s", 0)
            resp = ollama.chat(
                model=self._vision_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "Elenca SOLO gli oggetti fisici visibili. "
                        "Italiano, virgole, max 8 oggetti, solo sostantivi."
                    ),
                    "images": [img_b64],
                }],
                options={"temperature": 0.1, "num_predict": 60},
                keep_alive=keep_alive,
            )
            objects = self._extract_text(resp)
            log.info(f"🔍 Oggetti: {objects}")
            self._bus.publish(EventType.OBJECTS_DETECTED, {"objects": objects}, source="VisionService")
        except Exception as e:
            log.warning(f"VisionService object analysis: {e}")
        finally:
            if _OLLAMA_LOCK:
                _OLLAMA_LOCK.release()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _frame_to_b64(self, frame_rgb: np.ndarray) -> str:
        w = self._cfg.get("vision_llm.image_width",  320)
        h = self._cfg.get("vision_llm.image_height", 240)
        q = self._cfg.get("vision_llm.jpeg_quality",  70)
        bgr  = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        bgr  = cv2.resize(bgr, (w, h))
        _, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
        return base64.b64encode(buf.tobytes()).decode()

    @staticmethod
    def _extract_text(resp) -> str:
        try:
            return resp.message.content.strip()
        except AttributeError:
            return resp["message"]["content"].strip()

    def _resolve_vision_model(self) -> Optional[str]:
        priority = self._cfg.get("vision_llm.model_priority") or [
            "moondream", "llava:7b", "llava"
        ]
        try:
            import ollama
            available = [m.model for m in ollama.list().models]
            for pref in priority:
                for name in available:
                    if pref == name or name.startswith(pref + ":") or pref in name:
                        log.info(f"VisionService: using vision model '{name}'")
                        return name
        except Exception as e:
            log.warning(f"VisionService: could not list ollama models — {e}")
        return None

    def __repr__(self) -> str:
        return (
            f"<VisionService camera={self._camera.backend} "
            f"vision_model={self._vision_model}>"
        )
