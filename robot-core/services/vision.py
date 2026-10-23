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

With a picrawler body (`faces_fn`), the body finds and tracks faces next to its
camera (YuNet) and publishes an identity embedding per face (SFace); spooky
matches those with EmbeddingRecognizer and keeps the names. No frames are pulled
for faces — only when something asks for one (scene analysis, night watch, the
dashboard). The events and their payloads are the same as without a body.
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

# ── Vision-dedicated ollama lock (independent from MindService) ──────────────
# Vision uses moondream; Mind uses llama3.2:3b — both fit in 8 GB RAM.
# A separate lock prevents two concurrent moondream calls but allows mind to
# run its text model at the same time.
_VISION_LOCK = threading.Lock()

_FACE_FRESH_S = 0.5   # a body face last seen longer ago than this is not "in view"

# Kept for API compatibility with main.py (call is now a no-op)
def set_ollama_lock(lock: threading.Lock) -> None:
    pass   # vision uses _VISION_LOCK, not the shared text-model lock


# ══════════════════════════════════════════════════════════════════════════════
# Camera backend
# ══════════════════════════════════════════════════════════════════════════════

class CameraBackend:
    """
    Abstracts frame capture.
    Returns RGB uint8 frames always (not BGR) to avoid constant conversion.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 30, frame_fn=None):
        self._w   = width
        self._h   = height
        self._fps = fps
        self._cam = None
        self._backend = "none"
        # With a picrawler body, the body owns the camera (one process can open it)
        # and `frame_fn` returns its current RGB frame.
        self._frame_fn = frame_fn

    def open(self) -> bool:
        if self._frame_fn is not None:
            self._backend = "picrawler"
            log.info("Camera: frames from the picrawler body")
            return True

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
        if self._backend == "picrawler":
            return self._frame_fn()

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
            # Save raw crops (an embedding from the body is not an image: nothing to save)
            for i, crop in enumerate(face_crops):
                if crop.ndim < 2:
                    continue
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


class EmbeddingRecognizer:
    """
    Matches identity embeddings from the picrawler body (SFace: 128 floats, unit
    length) against each enrolled person's mean embedding. Same interface as
    FaceRecognizer, with an embedding where LBPH takes a gray crop.

    Cosine similarity → spooky's confidence: OpenCV's same-person threshold for
    SFace (0.363) lands on confidence_mid (0.55), 0.5 on confidence_high (0.80).
    Different people score around 0.2, i.e. confidence ≈ 0.25.
    """

    _SAME_PERSON = 0.363
    _MAX_LOOKS = 40          # per person: the most varied ones are kept
    _LEARN_EVERY_S = 30.0

    def __init__(self):
        self._means: Dict[str, np.ndarray] = {}
        self._samples: Dict[str, np.ndarray] = {}
        self._dirs: Dict[str, Path] = {}
        self._learnt_at: Dict[str, float] = {}
        self._lock = threading.RLock()

    @staticmethod
    def confidence(cosine: float) -> float:
        return float(min(1.0, max(0.0, 0.55 + (cosine - EmbeddingRecognizer._SAME_PERSON) * 0.25 / 0.137)))

    def enroll(self, person_id: str, embeddings: List[np.ndarray], save_dir: Path) -> None:
        stack = np.array([np.asarray(e, np.float32).ravel() for e in embeddings])
        np.save(save_dir / "embeddings.npy", stack)
        self._dirs[person_id] = save_dir
        self._remember(person_id, stack)

    def load_embedding(self, person_id: str, person_dir: Path) -> bool:
        path = person_dir / "embeddings.npy"
        if not path.exists():
            return False
        try:
            self._dirs[person_id] = person_dir
            self._remember(person_id, np.load(path))
            return True
        except Exception as e:
            log.error(f"EmbeddingRecognizer.load_embedding ({person_id}): {e}")
            return False

    def _remember(self, person_id: str, stack: np.ndarray) -> None:
        mean = stack.mean(axis=0)
        with self._lock:
            self._samples[person_id] = stack
            self._means[person_id] = mean / (np.linalg.norm(mean) or 1.0)

    def reinforce(self, person_id: str, embedding, now: Optional[float] = None) -> bool:
        """
        Learn a new look of someone recognised with confidence — another angle, other
        light, glasses — so recognition improves the more spooky sees them. At most one
        a person every 30 s; a look it already knows (cosine > 0.95) teaches nothing; past
        40 looks, the most redundant one makes room.
        """
        now = time.time() if now is None else now
        with self._lock:
            stack = self._samples.get(person_id)
            if stack is None or embedding is None or now - self._learnt_at.get(person_id, 0) < self._LEARN_EVERY_S:
                return False
            e = np.asarray(embedding, np.float32).ravel()
            e = e / (np.linalg.norm(e) or 1.0)
            norms = stack / np.maximum(np.linalg.norm(stack, axis=1, keepdims=True), 1e-9)
            if float((norms @ e).max()) > 0.95:
                return False
            if len(stack) >= self._MAX_LOOKS:
                redundancy = (norms @ norms.T).sum(axis=1)
                stack = np.delete(stack, int(np.argmax(redundancy)), axis=0)
            stack = np.vstack([stack, e])
            self._remember(person_id, stack)
            self._learnt_at[person_id] = now
            folder = self._dirs.get(person_id)
        if folder is not None:
            np.save(folder / "embeddings.npy", stack)
        return True

    def forget(self, person_id: str) -> None:
        with self._lock:
            self._means.pop(person_id, None)
            self._samples.pop(person_id, None)

    def predict(self, embedding) -> Tuple[Optional[str], float]:
        with self._lock:
            if not self._means or embedding is None:
                return None, 0.0
            e = np.asarray(embedding, np.float32).ravel()
            e = e / (np.linalg.norm(e) or 1.0)
            person_id, cosine = max(((pid, float(m @ e)) for pid, m in self._means.items()),
                                    key=lambda pc: pc[1])
        return person_id, self.confidence(cosine)


# ══════════════════════════════════════════════════════════════════════════════
# Face detector (OpenCV haar cascade)
# ══════════════════════════════════════════════════════════════════════════════

class FaceDetector:
    """
    OpenCV frontalface haar cascade. Fast, zero extra downloads.
    Returns list of (x, y, w, h) bounding boxes (in RGB frame coordinates).
    """

    def __init__(self, scale_factor: float = 1.1, min_neighbours: int = 5):
        data_dir = cv2.data.haarcascades
        path     = os.path.join(data_dir, "haarcascade_frontalface_default.xml")
        if not os.path.exists(path):
            log.error(f"FaceDetector: cascade not found at {path}")
        self._clf = cv2.CascadeClassifier(path)
        self._sf  = scale_factor
        self._mn  = min_neighbours

    def detect(self, frame_rgb: np.ndarray,
               min_size: int = 60) -> List[Tuple[int,int,int,int]]:
        gray  = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        # equalizeHist migliora la rilevazione in condizioni di luce scarsa
        gray  = cv2.equalizeHist(gray)
        faces = self._clf.detectMultiScale(
            gray, self._sf, self._mn,
            minSize=(min_size, min_size),
            flags=cv2.CASCADE_SCALE_IMAGE,
        )
        if len(faces) == 0:
            return []
        # Ordina per area decrescente (viso più grande prima)
        faces = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
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
        frame_fn=None,   # picrawler body: its camera frames instead of opening one
        faces_fn=None,   # picrawler body: the faces it sees (PicrawlerClient.faces)
        attention_fn=None,  # picrawler body: ask for fresh embeddings (enrolling)
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
            frame_fn = frame_fn,
        )

        # Detector + recognizer: the body's faces and embeddings, or a local cascade + LBPH
        self._faces_fn = faces_fn
        self._attention_fn = attention_fn
        # A conversation comes first: the Mac's model answers one request at a time, and
        # a question asked during a scene analysis waited over 30 s behind the photo.
        self._talking_until = 0.0
        for event in (EventType.COMMAND_PARSED, EventType.WAKE_WORD_DETECTED):
            bus.subscribe(event, lambda ev: setattr(self, "_talking_until", time.time() + 60))
        self._track_ids: Dict[int, Tuple[Optional[str], float]] = {}   # body track → best identity
        # Expression of the face in front: a candidate becomes current once it has lasted.
        self._expr_candidate: Tuple[Optional[str], float] = (None, 0.0)
        self._expr_current: Dict[str, object] = {}
        from services.expressions import ExpressionBaseline
        self._baseline = ExpressionBaseline(Path(self._db._root))   # each known person's resting face
        self._frame_at = 0.0
        if faces_fn is not None:
            self._detector   = None
            self._recognizer = EmbeddingRecognizer()
        else:
            det_cfg  = cfg.get("face.detector", {})
            self._detector = FaceDetector(
                scale_factor   = det_cfg.get("scale_factor",    1.15),
                min_neighbours = det_cfg.get("min_neighbours",  5),
            )
            self._recognizer = FaceRecognizer()

        # Intervals
        self._face_interval   = cfg.get("camera.face_detect_interval_s", 0.25)
        self._scene_interval  = cfg.get("vision_llm.scene_interval_s",   15)
        self._object_interval = cfg.get("vision_llm.object_interval_s",  20)

        # State
        self._active          = False
        self._last_frame_rgb: Optional[np.ndarray] = None
        self._frame_lock      = threading.Lock()
        self._consecutive_no_face = 0
        self._face_loss_threshold = 8   # frames before PERSON_LOST

        # Latest detection results (for annotation overlay)
        self._det_lock           = threading.Lock()
        self._last_faces:  List[Tuple] = []   # [(x,y,w,h, label, conf)]
        self._last_scene:  str = ""           # last scene description
        self._last_objects: str = ""          # last object list

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
        if self._faces_fn is None:
            self._capture_thread.start()   # with a body, frames are fetched only when asked for
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
        timeout_s: float = 30.0,
        progress_cb=None,   # callable(captured, total) — optional progress hook
    ) -> bool:
        """
        Interactively enroll a person: capture N face crops from live camera.
        Call from CLI / dashboard thread (blocks until done or timeout).
        progress_cb(captured, total) is called after each successful capture.
        """
        log.info(f"Enrolling '{display_name}' — look at the camera ({n_frames} samples needed)")
        if self._faces_fn is not None:
            return self._enroll_from_body(person_id, display_name, n_frames, timeout_s, progress_cb)
        crops: List[np.ndarray] = []
        deadline = time.time() + timeout_s
        last_progress = 0

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
                    log.info(f"Enroll '{display_name}': {len(crops)}/{n_frames}")
                    if progress_cb and len(crops) != last_progress:
                        last_progress = len(crops)
                        try:
                            progress_cb(len(crops), n_frames)
                        except Exception:
                            pass
            else:
                # No face detected — log periodically so user knows to look at camera
                elapsed = timeout_s - (deadline - time.time())
                if int(elapsed) % 5 == 0 and elapsed > 0:
                    log.info(f"Enroll '{display_name}': waiting for face ({len(crops)}/{n_frames} captured)")
            time.sleep(0.15)

        if len(crops) < n_frames // 2:
            log.error(f"Enrollment failed for '{display_name}': only {len(crops)}/{n_frames} samples")
            return False

        ok = self._db.enroll_person(person_id, display_name, crops, self._recognizer)
        if ok:
            # Reload embeddings so live face loop immediately recognizes the new person
            self._db.load_all_embeddings(self._recognizer)
            log.info(f"Enrollment complete for '{display_name}' — recognizer reloaded")
        return ok

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
        if self._faces_fn is not None and time.monotonic() - self._frame_at > 0.5:
            frame = self._camera.read_rgb()           # one frame from the body, on demand
            with self._frame_lock:
                self._last_frame_rgb, self._frame_at = frame, time.monotonic()
        with self._frame_lock:
            f = self._last_frame_rgb
            return f.copy() if f is not None else None

    def get_annotated_frame(self) -> Optional[np.ndarray]:
        """Return latest frame with face bboxes + labels drawn (BGR for MJPEG)."""
        frame = self.get_frame()
        if frame is None:
            return None
        # Convert RGB → BGR for cv2 drawing/encoding
        bgr = frame[:, :, ::-1].copy()
        with self._det_lock:
            faces = list(self._last_faces)
            objects = self._last_objects
        for (x, y, w, h, label, conf) in faces:
            color = (0, 220, 80) if label else (0, 180, 255)
            cv2.rectangle(bgr, (x, y), (x+w, y+h), color, 2)
            tag = f"{label} {conf:.0%}" if label else f"? {conf:.0%}" if conf > 0 else "persona"
            cv2.rectangle(bgr, (x, y-22), (x+w, y), color, -1)
            cv2.putText(bgr, tag, (x+4, y-5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,0,0), 1, cv2.LINE_AA)
        # Object overlay bottom strip
        if objects:
            h_f = bgr.shape[0]
            cv2.rectangle(bgr, (0, h_f-26), (bgr.shape[1], h_f), (20,20,20), -1)
            cv2.putText(bgr, objects[:90], (6, h_f-8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200,255,200), 1, cv2.LINE_AA)
        return bgr

    @property
    def last_scene(self) -> str:
        return self._last_scene

    @property
    def last_objects(self) -> str:
        return self._last_objects

    def _get_frame(self) -> Optional[np.ndarray]:
        return self.get_frame()

    # ── face detection + recognition loop ────────────────────────────────────

    def _face_loop(self) -> None:
        while self._active:
            if self._faces_fn is not None:
                self._process_body_faces(self._faces_fn())
            else:
                frame = self._get_frame()
                if frame is not None:
                    self._process_faces(frame)
            time.sleep(self._face_interval)

    def _process_body_faces(self, snap: Optional[dict]) -> None:
        """The body's faces → the same events _process_faces publishes from a frame."""
        faces = snap.get("faces", []) if snap and snap.get("status") == "ok" else []
        # The body keeps a face (and its id) for a while after it was last seen, so
        # that a glance away does not lose who it was; present means seen just now.
        seen = [f for f in faces if f.get("age_s", 0.0) <= _FACE_FRESH_S]
        if not faces:
            self._track_ids.clear()
        if not seen:
            self._consecutive_no_face += 1
            if self._consecutive_no_face == self._face_loss_threshold:
                with self._det_lock:
                    self._last_faces = []
                self._bus.publish(EventType.PERSON_LOST, {}, source="VisionService")
            return

        self._consecutive_no_face = 0
        frame_w, frame_h = snap.get("frame_size") or (320, 240)
        face = seen[0]   # the largest, as before
        person_id, conf = self._identify(face)
        self._track_ids = {f["id"]: self._track_ids[f["id"]] for f in faces if f["id"] in self._track_ids}

        bx, by, bw, bh = face["box"]
        x, y, w, h = (int(round(bx * frame_w)), int(round(by * frame_h)),
                      int(round(bw * frame_w)), int(round(bh * frame_h)))
        display_label = (self._db.display_name(person_id) or person_id) if person_id else None
        with self._det_lock:
            self._last_faces = [(x, y, w, h, display_label, conf)]

        base_payload = {
            "bbox":        [x, y, w, h],
            "center_x":    round((bx + bw / 2) * 2 - 1, 3),   # -1=left, +1=right
            "center_y":    round((by + bh / 2) * 2 - 1, 3),   # -1=top,  +1=bottom
            "confidence":  round(conf, 3),
            "track_id":    face["id"],
            "bearing_deg": face.get("bearing_deg"),
            "distance_m":  face.get("distance_m"),
        }
        expression = face.get("expression") if (face.get("expression_age_s") or 99) < 2.0 else None
        base_payload["expression"] = expression
        self._bus.publish(EventType.PERSON_DETECTED, {**base_payload, "person_id": person_id},
                          source="VisionService")
        name = (self._db.display_name(person_id) or person_id) if person_id and conf >= self._conf_high else None
        expr_conf, usual = face.get("expression_confidence") or 0.0, None
        if name and expression is not None and face.get("expressions"):
            relative = self._baseline.judge(person_id, face["expressions"])
            # Only where the model itself says neutral or is unsure: a clear "happy 0.97"
            # needs no help, and keeps the meaning it had.
            if relative and (expression == "neutral" or expr_conf < self._EXPR_MIN_CONF):
                expression, expr_conf, usual = relative
        self._track_expression(face["id"], person_id if name else None, name,
                               expression, expr_conf, usual=usual)
        if conf >= self._conf_high and person_id:
            self._bus.publish(EventType.PERSON_IDENTIFIED,
                              {**base_payload, "person_id": person_id,
                               "display_name": self._db.display_name(person_id) or person_id},
                              source="VisionService")
        else:
            self._bus.publish(EventType.PERSON_UNKNOWN, base_payload, source="VisionService")

    _EXPR_HOLD_S = 1.2       # an expression must last this long to count
    _EXPR_MIN_CONF = 0.5

    def _track_expression(self, track_id: int, person_id: Optional[str], name: Optional[str],
                          expression: Optional[str], confidence: float,
                          usual: Optional[float] = None) -> None:
        """
        Publish EXPRESSION_CHANGED when the face in front settles on a new expression.
        usual: set when the expression was read against the person's resting face
        (services/expressions.py) — then a probability under the usual threshold counts.
        """
        now = time.monotonic()
        if self._expr_current.get("track_id") != track_id:
            self._expr_current = {"track_id": track_id, "expression": None}
            self._expr_candidate = (None, now)
        if expression is None or (confidence < self._EXPR_MIN_CONF and usual is None):
            return
        if expression != self._expr_candidate[0]:
            self._expr_candidate = (expression, now)
            return
        held = now - self._expr_candidate[1]
        if held >= self._EXPR_HOLD_S and expression != self._expr_current.get("expression"):
            previous = self._expr_current.get("expression")
            self._expr_current = {"track_id": track_id, "expression": expression, "confidence": confidence,
                                  "person_id": person_id, "name": name, "since": now}
            self._bus.publish(EventType.EXPRESSION_CHANGED,
                              {"track_id": track_id, "person_id": person_id, "name": name,
                               "expression": expression, "confidence": round(confidence, 2),
                               "previous": previous, "usual": usual}, source="VisionService")
            log.info(f"Expression: {name or 'someone'} looks {expression} ({confidence:.2f}"
                     + (f", against a usual {usual:.2f})" if usual is not None else ")"))
        if expression == self._expr_current.get("expression"):
            self._expr_current["seen"] = now

    def expression_now(self) -> Optional[dict]:
        """How the person in front looks, if seen in the last few seconds."""
        cur = self._expr_current
        if cur.get("expression") and time.monotonic() - float(cur.get("seen", cur.get("since", 0))) < 5:
            return dict(cur)
        return None

    def known_in_view(self) -> Optional[str]:
        """The display name of a person recognised in front right now, if any."""
        for pid, conf in list(self._track_ids.values()):
            if pid and conf >= self._conf_high:
                return self._db.display_name(pid) or pid
        return None

    def track_identities(self) -> Dict[int, dict]:
        """Who each of the body's face tracks is, as far as spooky knows (for the dashboard)."""
        return {tid: {"person_id": pid, "name": (self._db.display_name(pid) or pid) if pid else None,
                      "confidence": round(conf, 3), "known": bool(pid) and conf >= self._conf_high}
                for tid, (pid, conf) in list(self._track_ids.items())}

    def forget_person(self, person_id: str) -> None:
        """Delete a person's face data; with a body, also from the live recognizer."""
        self._db.delete_person(person_id)
        if isinstance(self._recognizer, EmbeddingRecognizer):
            self._recognizer.forget(person_id)
        self._baseline.forget(person_id)          # their resting face goes with them
        self._track_ids.clear()

    def _identify(self, face: dict) -> Tuple[Optional[str], float]:
        """
        Best identity seen on this body track so far: one blurred embedding while
        the person turns their head does not make a known face unknown again.
        """
        best = self._track_ids.get(face["id"], (None, 0.0))
        person_id, conf = self._recognizer.predict(face.get("embedding"))
        if person_id and conf >= 0.85 and isinstance(self._recognizer, EmbeddingRecognizer):
            self._recognizer.reinforce(person_id, face.get("embedding"))   # learns as it goes
        if person_id is not None and conf > best[1]:
            best = (person_id, conf)
        self._track_ids[face["id"]] = best
        return best

    def _enroll_from_body(self, person_id: str, display_name: str, n_frames: int,
                          timeout_s: float, progress_cb=None) -> bool:
        """Enroll from the body's embeddings of the largest face, one per refresh (~2 a second)."""
        samples: List[np.ndarray] = []
        deadline = time.time() + timeout_s
        last = None
        if self._attention_fn is not None:
            self._attention_fn(timeout_s)    # the body slows embeddings for faces it has seen
        while len(samples) < n_frames and time.time() < deadline:
            snap = self._faces_fn()
            faces = snap.get("faces", []) if snap and snap.get("status") == "ok" else []
            emb = faces[0].get("embedding") if faces else None
            if emb is not None and emb != last:
                last = emb
                samples.append(np.asarray(emb, np.float32))
                log.info(f"Enroll '{display_name}': {len(samples)}/{n_frames}")
                if progress_cb:
                    try:
                        progress_cb(len(samples), n_frames)
                    except Exception:
                        pass
            time.sleep(0.15)

        if len(samples) < n_frames // 2:
            log.error(f"Enrollment failed for '{display_name}': only {len(samples)}/{n_frames} samples")
            return False
        ok = self._db.enroll_person(person_id, display_name, samples, self._recognizer)
        if ok:
            self._track_ids.clear()
            log.info(f"Enrollment complete for '{display_name}' — recognizer updated")
        return ok

    def _process_faces(self, frame: np.ndarray) -> None:
        faces = self._detector.detect(frame)

        if not faces:
            self._consecutive_no_face += 1
            if self._consecutive_no_face == self._face_loss_threshold:
                with self._det_lock:
                    self._last_faces = []
                self._bus.publish(EventType.PERSON_LOST, {}, source="VisionService")
            return

        self._consecutive_no_face = 0
        x, y, w, h = faces[0]   # process largest/first face only for now
        crop  = frame[y:y+h, x:x+w]
        gray  = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)

        person_id, conf = self._recognizer.predict(gray)

        # Store for overlay
        display_label = (self._db.display_name(person_id) or person_id) if person_id else None
        with self._det_lock:
            self._last_faces = [(x, y, w, h, display_label, conf)]

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
        last_resolve = time.time()
        while self._active:
            now = time.time()
            if not self._vision_model and now - last_resolve > 60:
                self._vision_model = self._resolve_vision_model()   # the Mac may be back
                last_resolve = now
            if now < self._talking_until:
                pass                                   # someone is talking to it: later
            elif self._vision_model:
                if now - last_scene >= self._scene_interval:
                    self._analyze_scene()
                    last_scene = time.time()
                if now - last_object >= self._object_interval:
                    self._analyze_objects()
                    last_object = time.time()
            else:
                # Nessun modello visivo: genera descrizione sintetica da face detection
                if now - last_scene >= self._scene_interval:
                    self._synthetic_scene()
                    last_scene = time.time()
            time.sleep(5)   # check every 5s

    def _analyze_scene(self) -> None:
        frame = self._get_frame()
        if frame is None:
            return
        ram = self._safety.get_ram_free_mb() if hasattr(self._safety, 'get_ram_free_mb') else 9999
        min_ram = self._cfg.get("vision_llm.min_ram_mb_to_infer", 800)
        if ram < min_ram:
            log.warning(f"VisionService: scene skip — RAM {ram}MB < {min_ram}MB")
            return

        if not _VISION_LOCK.acquire(blocking=True, timeout=30):
            log.debug("VisionService: scene skip — vision lock busy >30s")
            return
        try:
            from services import llm_host
            img_b64    = self._frame_to_b64(frame)
            keep_alive = self._cfg.get("vision_llm.keep_alive_s", 300)
            max_tok    = self._cfg.get("vision_llm.max_tokens", 80)
            resp = llm_host.client().chat(
                model=self._vision_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "You are Spooky, a small robot spider on a desk, looking through your camera. "
                        "In English, describe in one or two sentences what you see: objects, people, "
                        "the space. First person, start with 'I see'. Plain words, no emoji."
                    ),
                    "images": [img_b64],
                }],
                options={"temperature": 0.2, "num_predict": max_tok},
                keep_alive=keep_alive,
            )
            desc = self._extract_text(resp)
            log.info(f"👁️  {desc}")
            with self._det_lock:
                self._last_scene = desc
            self._bus.publish(EventType.SCENE_ANALYZED,
                              {"description": desc, "text": desc},
                              source="VisionService")
        except Exception as e:
            log.warning(f"VisionService scene analysis: {e}")
        finally:
            _VISION_LOCK.release()

    def _analyze_objects(self) -> None:
        frame = self._get_frame()
        if frame is None:
            return
        if not _VISION_LOCK.acquire(blocking=True, timeout=30):
            log.debug("VisionService: objects skip — vision lock busy >30s")
            return
        try:
            from services import llm_host
            img_b64    = self._frame_to_b64(frame)
            keep_alive = self._cfg.get("vision_llm.keep_alive_s", 300)
            resp = llm_host.client().chat(
                model=self._vision_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "List only the physical objects visible in this image. "
                        "English, comma-separated, at most 10 items, nouns only."
                    ),
                    "images": [img_b64],
                }],
                options={"temperature": 0.1, "num_predict": 60},
                keep_alive=keep_alive,
            )
            objects = self._extract_text(resp)
            log.info(f"🔍 Oggetti: {objects}")
            with self._det_lock:
                self._last_objects = objects
            self._bus.publish(EventType.OBJECTS_DETECTED,
                              {"objects": objects, "text": objects},
                              source="VisionService")
        except Exception as e:
            log.warning(f"VisionService object analysis: {e}")
        finally:
            _VISION_LOCK.release()

    def _synthetic_scene(self) -> None:
        """Fallback quando nessun modello visivo è disponibile.
        Costruisce una descrizione dalla face detection e dalla geometria."""
        with self._det_lock:
            faces = list(self._last_faces)
        if faces:
            labels = [f[4] for f in faces if f[4]]
            if labels:
                desc = f"I see {', '.join(labels)} in front of me."
            else:
                desc = "There is someone in front of me, but I don't know them."
        else:
            desc = "I don't see anyone in front of me right now."
        with self._det_lock:
            self._last_scene = desc
        self._bus.publish(EventType.SCENE_ANALYZED,
                          {"description": desc, "text": desc},
                          source="VisionService")

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
            from services import llm_host
            available = [m.model for m in llm_host.client().list().models]
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
