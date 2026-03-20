"""
services/audio.py — TTS, STT (Vosk), and wake-word detection.

Design:
  AudioOutput    — TTS via espeak-ng + aplay (thread-safe, unique temp files)
  AudioInput     — continuous Vosk STT stream + wake-word detection
  AudioService   — composes both, publishes bus events

Published events:
  WAKE_WORD_DETECTED  — robot name heard
  SPEECH_TRANSCRIBED  — raw transcript available
  COMMAND_PARSED      — clean command text ready for MindService
  TTS_STARTED
  TTS_FINISHED

Privacy rules:
  - No raw audio saved to disk
  - Vosk runs fully local
  - Only transcription text is published (not audio bytes)
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from core.bus import EventBus, EventType

log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# TTS — AudioOutput
# ══════════════════════════════════════════════════════════════════════════════

class AudioOutput:
    """
    Thread-safe TTS.

    Each say() call gets its own temp WAV file so concurrent calls don't
    corrupt each other. A single _tts_lock serialises actual playback so
    the speaker never overlaps.

    Backends (in order of preference):
      1. espeak-ng + aplay  (RPi, reliable, ALSA direct)
      2. espeak-ng + paplay (PulseAudio)
      3. pyttsx3            (macOS / cross-platform fallback)
      4. silent             (logs only — for CI/testing)
    """

    M_ESPEAK_APLAY  = "espeak_aplay"
    M_ESPEAK_PAPLAY = "espeak_paplay"
    M_PYTTSX3       = "pyttsx3"
    M_SILENT        = "silent"

    def __init__(self, bus: EventBus, language: str = "it", method: str = "auto"):
        self._bus        = bus
        self._lang       = language
        self._lock       = threading.Lock()
        self._aplay_dev  = self._detect_aplay_device()   # best non-HDMI card
        self._method     = self._detect(method)
        self._engine     = None   # pyttsx3 instance if used
        log.info(f"AudioOutput: method={self._method}, aplay_dev={self._aplay_dev}")

    @staticmethod
    def _detect_aplay_device() -> Optional[str]:
        """Return the first non-HDMI/non-vc4 ALSA output device (e.g. HifiBerry DAC)."""
        try:
            out = subprocess.check_output(
                ["aplay", "-l"], stderr=subprocess.DEVNULL, timeout=3
            ).decode(errors="replace")
            for line in out.splitlines():
                if not line.startswith("card "):
                    continue
                low = line.lower()
                if "hdmi" in low or "vc4" in low:
                    continue
                # e.g. "card 2: sndrpihifiberry ..."
                card_num = line.split(":")[0].split()[-1]
                dev = f"plughw:{card_num},0"
                log.info(f"AudioOutput: detected non-HDMI output → {dev} ({line.strip()})")
                return dev
        except Exception as e:
            log.debug(f"AudioOutput: aplay device detection failed — {e}")
        return None

    def say(self, text: str, wait: bool = True) -> None:
        """
        Speak `text`. Non-blocking by default (launches a daemon thread).
        Set wait=True to block until speech finishes.
        """
        if not text:
            return
        clean = self._sanitise(text)

        def _do():
            fd, wav = tempfile.mkstemp(suffix=".wav", prefix="spooky_tts_")
            os.close(fd)
            with self._lock:
                self._bus.publish(EventType.TTS_STARTED, {"text": clean}, source="AudioOutput")
                try:
                    self._speak(clean, wav)
                finally:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass
                    self._bus.publish(EventType.TTS_FINISHED, {}, source="AudioOutput")

        t = threading.Thread(target=_do, daemon=True, name="TTS")
        t.start()
        if wait:
            t.join()

    # ── backend dispatch ──────────────────────────────────────────────────────

    def _speak(self, text: str, wav_path: str) -> None:
        if self._method == self.M_ESPEAK_APLAY:
            self._espeak_aplay(text, wav_path)
        elif self._method == self.M_ESPEAK_PAPLAY:
            self._espeak_paplay(text, wav_path)
        elif self._method == self.M_PYTTSX3:
            self._pyttsx3_speak(text)
        else:
            log.debug(f"TTS [silent]: {text}")

    def _espeak_aplay(self, text: str, wav_path: str) -> None:
        try:
            subprocess.run(
                ["espeak-ng", "-v", self._lang, "-s", "145", "-w", wav_path, text],
                check=True, capture_output=True, timeout=15,
            )
        except Exception as e:
            log.error(f"TTS espeak-ng: {e}"); return

        # Build device list: detected non-HDMI card first, then fallbacks
        dev_list = []
        if self._aplay_dev:
            dev_list.append(["-D", self._aplay_dev])
        dev_list += [[], ["-D", "plug:default"], ["-D", "plughw:2,0"],
                     ["-D", "plughw:1,0"], ["-D", "plughw:0,0"]]

        played = False
        for dev_args in dev_list:
            try:
                subprocess.run(
                    ["aplay", "-q"] + dev_args + [wav_path],
                    check=True, capture_output=True, timeout=30,
                )
                log.debug(f"TTS: aplay ok with {dev_args or 'default'}")
                played = True; break
            except subprocess.CalledProcessError as ae:
                log.debug(f"TTS aplay {dev_args or 'default'}: {ae.stderr.decode(errors='replace')[:60]}")
        if not played:
            # Last resort: espeak-ng direct output (no aplay)
            log.warning("TTS: aplay failed on all devices — trying espeak-ng direct")
            try:
                subprocess.run(
                    ["espeak-ng", "-v", self._lang, "-s", "145", text],
                    check=True, capture_output=True, timeout=15,
                )
                played = True
            except Exception as e2:
                log.error(f"TTS: espeak-ng direct also failed — {e2}")

    def _espeak_paplay(self, text: str, wav_path: str) -> None:
        try:
            subprocess.run(
                ["espeak-ng", "-v", self._lang, "-s", "145", "-w", wav_path, text],
                check=True, capture_output=True, timeout=15,
            )
            subprocess.run(["paplay", wav_path], check=True, capture_output=True, timeout=30)
        except Exception as e:
            log.error(f"TTS espeak/paplay: {e}")

    def _pyttsx3_speak(self, text: str) -> None:
        try:
            if self._engine is None:
                import pyttsx3
                self._engine = pyttsx3.init()
            self._engine.say(text)
            self._engine.runAndWait()
        except Exception as e:
            log.error(f"TTS pyttsx3: {e}")

    # ── auto-detect backend ───────────────────────────────────────────────────

    def _detect(self, method: str) -> str:
        if method != "auto":
            return method
        if self._cmd_ok("espeak-ng") and self._cmd_ok("aplay"):
            return self.M_ESPEAK_APLAY
        if self._cmd_ok("espeak-ng") and self._cmd_ok("paplay"):
            return self.M_ESPEAK_PAPLAY
        try:
            import pyttsx3
            return self.M_PYTTSX3
        except ImportError:
            pass
        return self.M_SILENT

    @staticmethod
    def _cmd_ok(cmd: str) -> bool:
        try:
            subprocess.run(["which", cmd], check=True, capture_output=True)
            return True
        except Exception:
            return False

    @staticmethod
    def _sanitise(text: str) -> str:
        """Remove markup, ANSI codes, and overly long strings."""
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        text = re.sub(r"[*_`#]", "", text)
        return text[:400]   # cap length


# ══════════════════════════════════════════════════════════════════════════════
# STT + wake-word — AudioInput
# ══════════════════════════════════════════════════════════════════════════════

class AudioInput:
    """
    Continuous Vosk speech-to-text stream.

    State machine:
      IDLE   → listening for wake word
      ACTIVE → capturing full command (until silence timeout)

    Publishing:
      WAKE_WORD_DETECTED  when wake word heard in IDLE state
      SPEECH_TRANSCRIBED  every partial/final result (raw text)
      COMMAND_PARSED      when a complete command is assembled (ACTIVE → IDLE)
    """

    _STATE_IDLE   = "idle"
    _STATE_ACTIVE = "active"

    def __init__(
        self,
        bus: EventBus,
        wake_word: str = "spooky",
        vosk_model_path: str = "~/vosk-model-it",
        samplerate: int = 16000,
        blocksize: int = 8000,
        command_timeout_s: float = 6.0,
        device: Optional[int] = None,
    ):
        self._bus             = bus
        self._wake_word       = wake_word.lower()
        self._vosk_model_path = Path(vosk_model_path).expanduser()
        self._samplerate      = samplerate
        self._blocksize       = blocksize
        self._command_timeout = command_timeout_s
        self._device          = device   # None = sounddevice default

        self._active  = False
        self._state   = self._STATE_IDLE
        self._thread: Optional[threading.Thread] = None
        self._command_parts: List[str] = []
        self._last_speech_ts: float = 0.0

        # Callbacks registered externally (optional, events are preferred)
        self._on_command: Optional[Callable[[str], None]] = None

    def start(self) -> bool:
        if not self._vosk_model_path.exists():
            log.error(
                f"Vosk model not found at {self._vosk_model_path}. "
                f"Download: curl -L https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip | "
                f"unzip -d ~/"
            )
            return False
        try:
            import sounddevice  # noqa — check it's installed
            import vosk         # noqa
        except ImportError as e:
            log.error(f"AudioInput: missing dependency — {e}. pip install vosk sounddevice")
            return False

        self._active = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="AudioInput"
        )
        self._thread.start()
        log.info(f"AudioInput started (wake_word='{self._wake_word}', model={self._vosk_model_path})")
        return True

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("AudioInput stopped")

    def register_command_handler(self, fn: Callable[[str], None]) -> None:
        """Optional direct callback in addition to bus event."""
        self._on_command = fn

    # ── STT stream loop ───────────────────────────────────────────────────────

    @staticmethod
    def _resample_numpy(raw: bytes, src_rate: int, dst_rate: int = 16000) -> bytes:
        """Resample int16 mono bytes src_rate→dst_rate via numpy (Python 3.13-safe)."""
        if src_rate == dst_rate:
            return raw
        import numpy as np
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(samples) == 0:
            return raw
        target_len = max(1, int(round(len(samples) * dst_rate / src_rate)))
        resampled = np.interp(
            np.linspace(0, len(samples) - 1, target_len),
            np.arange(len(samples)),
            samples,
        ).astype(np.int16)
        return resampled.tobytes()

    def _run(self) -> None:
        import json
        import vosk
        import sounddevice as sd

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(self._vosk_model_path))
        # Vosk always expects 16000 Hz
        rec = vosk.KaldiRecognizer(model, 16000)

        # Auto-detect a working sample rate for the device
        hw_rate = self._detect_samplerate(sd)
        log.info(f"AudioInput: using hw_rate={hw_rate} Hz (Vosk target=16000 Hz)")
        self._bus.publish(EventType.MIC_STATE_CHANGED, {"state": "idle"}, source="AudioInput")

        # blocksize scaled to hw_rate so each chunk is ~0.5s
        blocksize = int(hw_rate * 0.5)
        stream_kwargs = dict(
            samplerate=hw_rate,
            blocksize=blocksize,
            dtype="int16",
            channels=1,
        )
        if self._device is not None:
            stream_kwargs["device"] = self._device

        try:
            with sd.RawInputStream(**stream_kwargs) as stream:
                while self._active:
                    data, _ = stream.read(blocksize)
                    raw = bytes(data)
                    # Resample to 16000 Hz if needed (numpy — Python 3.13 safe)
                    if hw_rate != 16000:
                        raw = self._resample_numpy(raw, hw_rate, 16000)
                    if rec.AcceptWaveform(raw):
                        result = json.loads(rec.Result())
                        text   = result.get("text", "").strip()
                        if text:
                            self._handle_text(text, final=True)
                    else:
                        partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                        if partial:
                            self._handle_text(partial, final=False)
        except Exception as e:
            log.error(f"AudioInput stream error: {e}")

    def _detect_samplerate(self, sd) -> int:
        """Try rates in order; return the first one the device accepts."""
        candidates = [16000, 44100, 48000, 32000, 22050, 8000]
        dev = self._device  # None = default input

        # Log available devices once for visibility
        try:
            devs = sd.query_devices()
            inputs = [f"  {i}: {d['name']} ({int(d['default_samplerate'])}Hz)"
                      for i, d in enumerate(devs) if d['max_input_channels'] > 0]
            log.info("AudioInput devices:\n" + "\n".join(inputs) if inputs else "  (none)")
            if dev is None and devs:
                di = sd.default.device[0]
                log.info(f"AudioInput: default input device [{di}]: {devs[di]['name']}")
        except Exception as e:
            log.debug(f"AudioInput: device query failed — {e}")

        for rate in candidates:
            try:
                sd.check_input_settings(device=dev, samplerate=rate,
                                        channels=1, dtype="int16")
                log.info(f"AudioInput: sample rate {rate} Hz accepted by device")
                return rate
            except Exception:
                continue
        log.warning("AudioInput: could not detect sample rate, falling back to 44100")
        return 44100

    # ── state machine ──────────────────────────────────────────────────────────

    def _handle_text(self, text: str, final: bool) -> None:
        self._bus.publish(
            EventType.SPEECH_TRANSCRIBED,
            {"text": text, "final": final},
            source="AudioInput",
        )

        if self._state == self._STATE_IDLE:
            if self._wake_word in text.lower():
                log.info(f"Wake word detected: '{text}'")
                self._bus.publish(
                    EventType.WAKE_WORD_DETECTED,
                    {"transcript": text},
                    source="AudioInput",
                )
                self._bus.publish(
                    EventType.MIC_STATE_CHANGED,
                    {"state": "listening"},
                    source="AudioInput",
                )
                # Strip wake word and keep remainder as start of command
                remainder = re.sub(
                    re.escape(self._wake_word), "", text, flags=re.IGNORECASE
                ).strip()
                self._state = self._STATE_ACTIVE
                self._command_parts = [remainder] if remainder else []
                self._last_speech_ts = time.time()

        elif self._state == self._STATE_ACTIVE:
            if final and text:
                self._command_parts.append(text)
                self._last_speech_ts = time.time()

            # Silence timeout → emit command
            if time.time() - self._last_speech_ts >= self._command_timeout:
                self._emit_command()

    def _emit_command(self) -> None:
        cmd = " ".join(p for p in self._command_parts if p).strip()
        self._state         = self._STATE_IDLE
        self._command_parts = []

        if not cmd:
            self._bus.publish(EventType.MIC_STATE_CHANGED, {"state": "idle"}, source="AudioInput")
            return

        log.info(f"Command: '{cmd}'")
        self._bus.publish(EventType.MIC_STATE_CHANGED, {"state": "thinking"}, source="AudioInput")
        self._bus.publish(
            EventType.COMMAND_PARSED,
            {"command": cmd},
            source="AudioInput",
        )
        if self._on_command:
            try:
                self._on_command(cmd)
            except Exception as e:
                log.error(f"AudioInput command handler: {e}")
        # Return to idle once command has been dispatched
        self._bus.publish(EventType.MIC_STATE_CHANGED, {"state": "idle"}, source="AudioInput")


# ══════════════════════════════════════════════════════════════════════════════
# AudioService — composes output + input
# ══════════════════════════════════════════════════════════════════════════════

class AudioService:
    """
    Facade over AudioOutput (TTS) and AudioInput (STT + wake word).

    Usage:
        audio = AudioService(bus, cfg)
        audio.start()
        audio.say("Ciao mondo")
    """

    def __init__(self, bus: EventBus, cfg):
        robot_cfg = cfg.get("robot", {})
        audio_cfg = cfg.get("audio", {})

        lang       = robot_cfg.get("language", "it")
        tts_method = audio_cfg.get("tts_method", "auto")
        wake_word  = audio_cfg.get("wake_word", "spooky")
        vosk_model = audio_cfg.get("vosk_model", "~/vosk-model-it")
        samplerate = audio_cfg.get("samplerate", 16000)
        blocksize  = audio_cfg.get("blocksize",  8000)
        device     = audio_cfg.get("mic_device", None)   # None = sounddevice default

        self._out = AudioOutput(bus, language=lang, method=tts_method)
        self._inp = AudioInput(
            bus,
            wake_word=wake_word,
            vosk_model_path=vosk_model,
            samplerate=samplerate,
            blocksize=blocksize,
            device=device,
        )

    def start(self) -> None:
        ok = self._inp.start()
        if not ok:
            log.warning("AudioService: STT/wake-word unavailable (TTS still active)")
        log.info("AudioService ready")

    def stop(self) -> None:
        self._inp.stop()
        log.info("AudioService stopped")

    def say(self, text: str, wait: bool = False) -> None:
        self._out.say(text, wait=wait)

    @property
    def output(self) -> AudioOutput:
        return self._out

    @property
    def input(self) -> AudioInput:
        return self._inp

    def __repr__(self) -> str:
        return (
            f"<AudioService tts={self._out._method} "
            f"stt={'running' if self._inp._active else 'stopped'}>"
        )
