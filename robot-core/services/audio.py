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

import json
import logging
import os
import queue
import re
import struct
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

    M_PIPER         = "piper"
    M_DROID         = "droid"     # spooky's own whistle-and-click language (services/droid_voice.py)
    M_ESPEAK_APLAY  = "espeak_aplay"
    M_ESPEAK_PAPLAY = "espeak_paplay"
    M_PYTTSX3       = "pyttsx3"
    M_SILENT        = "silent"

    # Baymax-inspired voice: calm, warm, slightly deep
    _ESPEAK_VOICE     = "it+m3"
    _ESPEAK_SPEED     = "115"
    _ESPEAK_PITCH     = "48"
    _ESPEAK_AMPLITUDE = "68"

    # Piper TTS (neural, high quality)
    _PIPER_VOICE     = "it_IT-riccardo-x_low"
    _PIPER_VOICES_DIR = "~/piper-voices"

    def __init__(self, bus: EventBus, language: str = "it", method: str = "auto",
                 speaker_pin: Optional[int] = None, piper_voice: Optional[str] = None,
                 piper_speaker: Optional[str] = None):
        self._bus        = bus
        self._piper_voice = piper_voice or self._PIPER_VOICE
        self._piper_speaker = piper_speaker   # multi-speaker voices (VCTK: p232, …)
        self._lang       = language
        self._lock       = threading.Lock()
        # One speaker, in order: a reply now arrives a sentence at a time, and a thread
        # per sentence waiting on a lock could say the second before the first.
        self._queue: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._speaker, daemon=True, name="TTS").start()
        self._aplay_dev  = self._detect_aplay_device()   # best non-HDMI card
        self._method     = self._detect(method)
        self._engine     = None   # pyttsx3 instance if used
        self._piper      = None   # loaded once: ~1-2 s per load on the Pi
        self._volume_file = Path(__file__).resolve().parent.parent / "data" / "audio.json"
        self._volume     = self._load_volume()
        if speaker_pin is not None:
            self._enable_speaker(int(speaker_pin))
        log.info(f"AudioOutput: method={self._method}, aplay_dev={self._aplay_dev}")

    # ── volume ────────────────────────────────────────────────────────────────
    # The Robot HAT's DAC has no volume control, so it is done here: the samples are
    # scaled before playing, on a square curve so that 50 sounds like half. At 0
    # nothing is played, but the words still reach the chat.

    @property
    def method(self) -> str:
        return self._method

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, volume: float) -> int:
        self._volume = int(max(0, min(100, round(volume))))
        try:
            self._volume_file.parent.mkdir(parents=True, exist_ok=True)
            self._volume_file.write_text(json.dumps({"volume": self._volume}))
        except OSError as e:
            log.warning(f"AudioOutput: volume not saved: {e}")
        log.info(f"AudioOutput: volume {self._volume}")
        return self._volume

    def _load_volume(self) -> int:
        try:
            return int(json.loads(self._volume_file.read_text())["volume"])
        except (OSError, ValueError, KeyError):
            return 70

    def _apply_volume(self, wav_path: str) -> bool:
        """Scale the file in place; False when muted (nothing to play)."""
        if self._volume <= 0:
            return False
        if self._volume >= 100:
            return True
        import wave as _wave
        import numpy as np
        try:
            with _wave.open(wav_path, "rb") as r:
                params, frames = r.getparams(), r.readframes(r.getnframes())
            if params.sampwidth != 2:
                return True
            gain = (self._volume / 100.0) ** 2
            pcm = (np.frombuffer(frames, np.int16).astype(np.float32) * gain).astype(np.int16)
            with _wave.open(wav_path, "wb") as w:
                w.setparams(params)
                w.writeframes(pcm.tobytes())
        except Exception as e:
            log.debug(f"AudioOutput: volume not applied: {e}")
        return True

    def _enable_speaker(self, pin: int) -> None:
        """
        The Robot HAT's amplifier is off until its enable pin is driven high — what
        SunFounder's robot_hat.enable_speaker() does. Then a moment of silence, as
        it does too, so the DAC is not left feeding the amplifier noise.
        """
        try:
            subprocess.run(["pinctrl", "set", str(pin), "op", "dh"], check=True,
                           capture_output=True, timeout=3)
            if self._aplay_dev:
                silence = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + 16000, b"WAVE", b"fmt ", 16,
                                      1, 1, 16000, 32000, 2, 16, b"data", 16000) + bytes(16000)
                subprocess.run(["aplay", "-q", "-D", self._aplay_dev, "-"], input=silence,
                               capture_output=True, timeout=5)
            log.info(f"AudioOutput: speaker amplifier on (GPIO{pin})")
        except Exception as e:
            log.warning(f"AudioOutput: could not switch the speaker on (GPIO{pin}): {e}")

    @staticmethod
    def _detect_aplay_device() -> Optional[str]:
        """
        Return the best non-HDMI ALSA output device using stable CARD= name
        (not card number, which changes across reboots).
        """
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
                # e.g. "card 2: sndrpihifiberry [snd_rpi_hifiberry_dac], device 0: ..."
                # Use CARD=name syntax so it works regardless of card number
                parts = line.split(":")
                if len(parts) < 2:
                    continue
                card_name = parts[1].strip().split()[0]   # e.g. "sndrpihifiberry"
                dev = f"plughw:CARD={card_name},DEV=0"
                log.info(f"AudioOutput: detected non-HDMI output → {dev}")
                return dev
        except Exception as e:
            log.debug(f"AudioOutput: aplay device detection failed — {e}")
        return None

    @staticmethod
    def _test_aplay_device(dev_args: list, wav_path: str) -> bool:
        """Try aplay and return True only if it actually produces audio (non-HDMI check)."""
        try:
            result = subprocess.run(
                ["aplay", "-q"] + dev_args + [wav_path],
                capture_output=True, timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    def say(self, text: str, wait: bool = True, feeling: Optional[str] = None,
            continues: bool = False) -> None:
        """
        Speak `text`, after whatever is already queued. Set wait=True to block until
        it has been said. `feeling` sets the droid voice's tone (happy, sad, question,
        alert, sleepy); otherwise it is read from the words. `continues`: the next
        sentence of the same reply (the chat shows it as one message).
        """
        if not text:
            return
        done = threading.Event()
        self._queue.put((self._sanitise(text), feeling, continues, done))
        if wait:
            done.wait()

    def _speaker(self) -> None:
        while True:
            clean, feeling, continues, done = self._queue.get()
            fd, wav = tempfile.mkstemp(suffix=".wav", prefix="spooky_tts_")
            os.close(fd)
            with self._lock:
                self._bus.publish(EventType.TTS_STARTED, {"text": clean, "continues": continues}, source="AudioOutput")
                try:
                    self._speak(clean, wav, feeling)
                except Exception as e:
                    log.error(f"TTS: {e}")
                finally:
                    try:
                        os.unlink(wav)
                    except OSError:
                        pass
                    self._bus.publish(EventType.TTS_FINISHED, {}, source="AudioOutput")
                    done.set()

    # ── backend dispatch ──────────────────────────────────────────────────────

    def _speak(self, text: str, wav_path: str, feeling: Optional[str] = None) -> None:
        if self._method == self.M_PIPER:
            self._piper_speak(text, wav_path)
        elif self._method == self.M_DROID:
            self._droid_speak(text, wav_path, feeling)
        elif self._method == self.M_ESPEAK_APLAY:
            self._espeak_aplay(text, wav_path)
        elif self._method == self.M_ESPEAK_PAPLAY:
            self._espeak_paplay(text, wav_path)
        elif self._method == self.M_PYTTSX3:
            self._pyttsx3_speak(text)
        else:
            log.debug(f"TTS [silent]: {text}")

    def _piper_speak(self, text: str, wav_path: str) -> None:
        """Neural TTS via piper-tts Python package. Falls back to espeak-ng."""
        try:
            import wave as _wave
            from piper.voice import PiperVoice
            if self._piper is None:
                voices_dir = Path(self._PIPER_VOICES_DIR).expanduser()
                model_onnx = voices_dir / f"{self._piper_voice}.onnx"
                if not model_onnx.exists():
                    raise FileNotFoundError(f"Piper model missing: {model_onnx}")
                self._piper = PiperVoice.load(str(model_onnx), use_cuda=False)
            voice = self._piper
            with _wave.open(wav_path, "wb") as wf:
                if hasattr(voice, "synthesize_wav"):      # piper-tts ≥ 1.3
                    syn = None
                    if self._piper_speaker:
                        from piper import SynthesisConfig
                        syn = SynthesisConfig(speaker_id=voice.config.speaker_id_map[self._piper_speaker])
                    voice.synthesize_wav(text, wf, syn_config=syn)
                else:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(voice.config.sample_rate)
                    voice.synthesize(text, wf)
            self._play_wav(wav_path)
        except Exception as e:
            log.warning(f"piper: {e} — using espeak-ng")
            self._espeak_aplay(text, wav_path)

    def chirp(self) -> None:
        """
        A short rising blip: "I heard my name, go on". Played without the TTS events
        on purpose: they would mark the next second as its own voice and swallow the
        start of the command; a blip has no words for the recogniser to mistake.
        """
        if self._volume <= 0 or not self._aplay_dev:
            return

        def _do():
            import wave as _wave
            import numpy as np
            from services.droid_voice import RATE, _tone
            blip = (np.concatenate([_tone(900, 1500, 0.09, "whistle"), np.zeros(int(0.03 * RATE)),
                                    _tone(1200, 1900, 0.07, "blip")]) * 0.6 * 32767).astype(np.int16)
            fd, wav = tempfile.mkstemp(suffix=".wav", prefix="spooky_chirp_")
            os.close(fd)
            try:
                with _wave.open(wav, "wb") as wf:
                    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(RATE)
                    wf.writeframes(blip.tobytes())
                if self._apply_volume(wav):
                    subprocess.run(["aplay", "-q", "-D", self._aplay_dev, wav], capture_output=True, timeout=5)
            finally:
                os.unlink(wav)

        threading.Thread(target=_do, daemon=True, name="Chirp").start()

    def _droid_speak(self, text: str, wav_path: str, feeling: Optional[str] = None) -> None:
        """Spooky's own language: the words become whistles and clicks; the text stays in the chat."""
        import wave as _wave
        from services.droid_voice import RATE, render
        with _wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(render(text, feeling).tobytes())
        self._play_wav(wav_path)

    def _play_wav(self, wav_path: str) -> None:
        """Play a WAV file via the best available ALSA device."""
        if not self._apply_volume(wav_path):
            return
        dev_list: list = []
        if self._aplay_dev:
            dev_list.append(["-D", self._aplay_dev])
        dev_list += [
            ["-D", "plughw:CARD=sndrpihifiberry,DEV=0"],
            ["-D", "plughw:CARD=b1,DEV=0"],
            ["-D", "plughw:CARD=Headphones,DEV=0"],
        ]
        for dev_args in dev_list:
            r = subprocess.run(
                ["aplay", "-q"] + dev_args + [wav_path],
                capture_output=True, timeout=30,
            )
            if r.returncode == 0:
                return
        log.warning("TTS: all aplay devices failed")

    def _espeak_aplay(self, text: str, wav_path: str) -> None:
        try:
            subprocess.run(
                ["espeak-ng",
                 "-v", self._ESPEAK_VOICE,
                 "-s", self._ESPEAK_SPEED,
                 "-p", self._ESPEAK_PITCH,
                 "-a", self._ESPEAK_AMPLITUDE,
                 "-w", wav_path, text],
                check=True, capture_output=True, timeout=15,
            )
        except Exception as e:
            log.error(f"TTS espeak-ng: {e}"); return

        self._play_wav(wav_path)

    def _espeak_paplay(self, text: str, wav_path: str) -> None:
        try:
            subprocess.run(
                ["espeak-ng",
                 "-v", self._ESPEAK_VOICE,
                 "-s", self._ESPEAK_SPEED,
                 "-p", self._ESPEAK_PITCH,
                 "-a", self._ESPEAK_AMPLITUDE,
                 "-w", wav_path, text],
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
        # piper-tts (neural, highest quality) — requires model file
        voices_dir = Path(self._PIPER_VOICES_DIR).expanduser()
        model_onnx = voices_dir / f"{self._piper_voice}.onnx"
        if model_onnx.exists():
            try:
                from piper.voice import PiperVoice  # noqa
                log.info("AudioOutput: piper-tts available — using neural voice")
                return self.M_PIPER
            except ImportError:
                log.debug("AudioOutput: piper-tts package not installed")
        # espeak-ng (Baymax-tuned parameters)
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


# ── Wake word ─────────────────────────────────────────────────────────────────
#
# "Spooky" is an English word and the Italian Vosk model has no such word: tested
# with three voices it wrote "sporchi", "spot", "spot chi", "spock chi", "sfoghi" —
# never "spooky". So the name is matched by its sound: the first words of what was
# heard, reduced to a consonant skeleton (Italian spelling: ch/gh/c+aou → K,
# f/v/b → P, d → T, l → R), must start with S-P and reach a K or a T within the
# next two consonants. The price: a sentence that *starts* with "sporco" or "spot"
# wakes it too.

_FILLERS = {"e", "ehi", "hey", "ei", "eh", "oh", "o", "ciao", "ok", "dai"}
_KEY = [(r"ch|gh|ck|c(?=[aou])|q|g(?=[aou])|k|g$", "K"), (r"[fvbp]", "P"), (r"[td]", "T"),
        (r"[rl]", "R"), (r"[mn]", "N"), (r"[sz]", "S"), (r"[cg]", "K")]


def _skeleton(word: str) -> str:
    w = word.lower()
    for pattern, sub in _KEY:
        w = re.sub(pattern, sub, w)
    w = re.sub(r"[^KPTRNS]", "", w)
    return re.sub(r"(.)\1+", r"\1", w)         # double consonants count once


def wake_match(text: str, wake_word: str = "spooky") -> Optional[str]:
    """What follows the name if `text` starts by calling spooky, else None."""
    words = text.lower().split()
    if wake_word.lower() in words:
        return " ".join(words[words.index(wake_word.lower()) + 1:])
    while words and words[0] in _FILLERS:
        words = words[1:]
    if words and words[0].startswith("e") and _skeleton(words[0][1:]).startswith("SP"):
        words[0] = words[0][1:]                  # "espo chi": the "e" of "ehi" swallowed
    if not words:
        return None
    # Vosk often splits the name: "spot chi", "spock chi" — the second half is a lone K.
    n = 2 if len(words) > 1 and _skeleton(words[1]) == "K" else 1
    key = _skeleton("".join(words[:n]))
    if key.startswith("SP") and any(c in key[2:4] for c in "KT") and len(words) > n - 1:
        if n == 2 or len(words) > 1:             # a lone "spot" is not a call
            return " ".join(words[n:])
    return None


class WakeSpotter:
    """
    The name, listened for on its own. Free recognition with the Italian model never
    writes "spooky": on the user's voice, five "Spooky" came out as "pochi", "sbuchi",
    "buchi", "sfoci", "sporchi". The word is in the model's vocabulary, only improbable
    in Italian, so a second recogniser on the same audio, whose grammar allows only the
    name or [unk], is forced to choose. Measured on 90 s from the robot's microphone:
    all five found with confidence 1.0, about a second after each, and nothing in a
    minute of office noise. Its partial results never showed the name at all, so only
    final results count, and only confident ones.

    Both recognisers time their words on the same stream, so the command is what the
    free one heard from just after the name.

    The grammar recogniser must choose between the name and [unk], so in other
    people's flowing speech it sometimes chooses the name — at confidence 1.00, so
    confidence cannot tell (2026-10-23, 14:04–14:24: "…provate a chiamare il servizio
    clienti", "…primitivo e del demanio allora navigare nel sito…"). Someone calling
    it starts with the name, or a greeting before it; a name glued to the end of two
    or more words is the middle of somebody's sentence, and is ignored. (Asking the
    language model whether the words were meant for the robot was measured and does
    not work: on short garbled fragments gemma3 put "cosa vedi" among the overheard.)

    What does tell them apart is how long the "name" lasts. Saying "spooky" takes half
    a second; forced to choose, the grammar recogniser stretches the name over whole
    stretches of somebody else's talk. Measured 2026-10-23: the user's calls 0.31,
    0.51, 0.54, 0.66 s; colleagues' talk 1.98, 3.09, 3.96, 4.11, 4.74, 6.45, 7.68,
    8.16, 17.10 s. Only a name of 0.2–1.2 s counts.
    """

    _NAME_S = 0.3          # the command starts this long after the name begins
    _NAME_LEN_S = (0.2, 1.2)  # how long a spoken "spooky" lasts; longer is other people's talk
    _GLUED_S = 0.3         # a word ending this close before the name runs into it
    _GREETINGS = frozenset({"ciao", "ehi", "ei", "hey", "oh", "senti", "ok", "okay", "salve",
                            "buongiorno", "buonasera", "dai"})

    def __init__(self, wake_word: str = "spooky", min_conf: float = 0.9, wait_s: float = 2.5,
                 strict_fn: Optional[Callable[[], bool]] = None, strict_conf: float = 0.97) -> None:
        self._word = wake_word.lower()
        self._min_conf = min_conf
        # While the body moves, its servos come out as the name at confidence up to 0.87
        # (measured during autonomy); the user's real calls came out at 1.00.
        self._strict_fn = strict_fn
        self._strict_conf = strict_conf
        self._wait = wait_s
        self._name_start: Optional[float] = None   # a name heard, its command not yet taken
        self._name_s = 0.0                         # how long the grammar recogniser's "spooky" lasted
        self._utterances: List[list] = []          # recent free-recognition words

    def spotted(self, words: list) -> None:
        """Words of a final result from the grammar recogniser."""
        for w in words:
            if w.get("word") == self._word:
                strict = self._strict_fn is not None and self._strict_fn()
                length = float(w.get("end", w["start"])) - float(w["start"])
                sure = w.get("conf", 0.0) >= (self._strict_conf if strict else self._min_conf)
                spoken = self._NAME_LEN_S[0] <= length <= self._NAME_LEN_S[1]
                ok = sure and spoken
                log.info(f"Name spotted at {w['start']:.1f}s, {length:.2f} s long, confidence "
                         f"{w.get('conf', 0.0):.2f}{' (body moving)' if strict else ''}"
                         f"{'' if sure else ' — too unsure'}{'' if spoken or not sure else ' — too long or short for the name'}")
                if ok:
                    self._name_start = float(w["start"])
                    self._name_s = float(w.get("end", w["start"])) - float(w["start"])
                    return

    def heard(self, words: list) -> None:
        """Words of a final result from the free recogniser."""
        if words:
            self._utterances = (self._utterances + [words])[-4:]

    def poll(self, now_s: float) -> Optional[str]:
        """The command after the name once it is known ("" if nothing followed), else None."""
        if self._name_start is None:
            return None
        begin = self._name_start + self._NAME_S
        covering = [u for u in self._utterances if float(u[-1]["end"]) >= begin]
        if not covering and now_s - self._name_start < self._wait:
            return None                            # the free recogniser has not finished it yet
        words = covering[0] if covering else []
        name_at = self._name_start
        self._name_start = None
        self._utterances = []
        # Before the name: what ended before it began (the free recogniser's own
        # spelling of the name starts with it, give or take a tenth of a second).
        before = [w for w in words if float(w["start"]) < name_at - 0.15 and float(w["end"]) <= name_at + 0.1]
        command = [w["word"] for w in words if float(w["start"]) >= begin]
        # And how the free recogniser spelt the name itself ("sporchi", "pochi"… for a
        # real call): logged with the name's length to find what tells a call from a
        # song or a conversation — on 2026-10-23 the false spots came at the start of
        # utterances too, where the glued-name rule cannot see them.
        heard_as = [w["word"] for w in words if name_at - 0.15 <= float(w["start"]) < begin]
        context = (f"'{' '.join(w['word'] for w in before[-4:])} | {' '.join(heard_as) or '-'} "
                   f"({self._name_s:.2f} s) | {' '.join(command)}'")
        if len(before) >= 2 and name_at - float(before[-1]["end"]) < self._GLUED_S \
                and before[-1]["word"] not in self._GREETINGS:
            log.info(f"Name in the middle of a sentence — ignored: {context}")
            return None
        # Nothing heard where the name was, and nothing after it: a cough, a chair, a
        # keyboard — the grammar recogniser has to put the name somewhere. A real call
        # is heard as something ("sfoghi", "buchi", "seduto"…) or has a command after
        # it. Measured 2026-10-23 16:25–17:41: 16 of about 20 false wakes were this,
        # and 6 of the user's 7 calls had a word or a command.
        if not heard_as and not command:
            log.info(f"Name with nothing said around it — ignored: {context}")
            return None
        log.info(f"Name taken: {context}")
        return " ".join(command)


# ══════════════════════════════════════════════════════════════════════════════
# STT + wake-word — AudioInput
# ══════════════════════════════════════════════════════════════════════════════

class LoudnessWatch:
    """
    A sudden loud sound — a clap, a bang, something dropped — in a chunk of audio.

    Levels per 20 ms frame, in dB below full scale. Loud means all three at once:
    at least `floor_dbfs`; at least `above_db` over the room (a slow average of the
    chunks' median frames, so a loud chunk barely moves it); and reached from
    `rise_db` lower within one frame — a clap starts at once, a voice swells, so
    talking near it, even loudly, is not a bang. Measured on the robot's USB mic
    (2026-10-23): the room at -42 dBFS, never above -29, at most 10 dB from one
    frame to the next.
    """

    FRAME = 320                                    # 20 ms at 16 kHz

    def __init__(self, floor_dbfs: float = -18.0, above_db: float = 22.0, rise_db: float = 15.0) -> None:
        self.floor_dbfs, self.above_db, self.rise_db = floor_dbfs, above_db, rise_db
        self.room_db: Optional[float] = None
        self._last_db = -120.0

    def feed(self, samples) -> Optional[dict]:
        import numpy as np
        n = len(samples) // self.FRAME * self.FRAME
        if n == 0:
            return None
        frames = np.asarray(samples[:n], dtype=np.float32).reshape(-1, self.FRAME)
        db = 20.0 * np.log10(np.sqrt((frames * frames).mean(axis=1)) / 32768.0 + 1e-9)
        before = np.concatenate([[self._last_db], db[:-1]])
        i = int(np.argmax(db))
        peak, rise = float(db[i]), float(db[i] - before[i])
        self._last_db = float(db[-1])
        quiet = float(np.median(db))
        if self.room_db is None:
            self.room_db = quiet
            return None
        room = self.room_db
        self.room_db += 0.05 * (quiet - room)      # ~10 s to follow the room
        if peak >= self.floor_dbfs and peak - room >= self.above_db and rise >= self.rise_db:
            return {"db": round(peak, 1), "room_db": round(room, 1), "rise_db": round(rise, 1)}
        return None


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
        moving_fn: Optional[Callable[[], bool]] = None,
        loudness: Optional[LoudnessWatch] = None,
    ):
        self._bus             = bus
        self._loudness        = loudness or LoudnessWatch()
        self._wake_word       = wake_word.lower()
        self._vosk_model_path = Path(vosk_model_path).expanduser()
        self._samplerate      = samplerate
        self._blocksize       = blocksize
        self._command_timeout = command_timeout_s
        self._device          = device   # None = sounddevice default

        self._active  = False
        self._state   = self._STATE_IDLE
        self._thread: Optional[threading.Thread] = None
        # For health(): when the last half second of audio arrived, and why there is none.
        self.heard_at = 0.0
        self.model_loaded = False
        self.problem = ""
        self._command_parts: List[str] = []
        self._last_speech_ts: float = 0.0

        # Callbacks registered externally (optional, events are preferred)
        self._on_command: Optional[Callable[[str], None]] = None

        # Its own voice comes back through the mic: while it speaks, and a moment
        # after, what is heard is marked as its own and never wakes it or commands it.
        self._own_until = 0.0
        # Its own motors too: while the body moves, the name needs more confidence
        # (WakeSpotter's strict threshold) — vetoing it outright ignored the user's real
        # calls while the robot leaned towards them. And after waking, its own blip must
        # not wake it again.
        self._moving_fn = moving_fn
        self._moved_at = -1e9
        self._refractory_until = 0.0
        bus.subscribe(EventType.TTS_STARTED, lambda ev: setattr(self, "_own_until", float("inf")))
        bus.subscribe(EventType.TTS_FINISHED, lambda ev: setattr(self, "_own_until", time.time() + self._OWN_TAIL_S))

    def start(self) -> bool:
        if not self._vosk_model_path.exists():
            self.problem = f"no Vosk model at {self._vosk_model_path}"
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
            self.problem = f"missing {e.name}"
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

    def _listen_for_bangs(self, raw: bytes) -> None:
        """A sudden loud sound → UNUSUAL_SOUND; skills/startle_skill.py decides what to do."""
        import numpy as np
        bang = self._loudness.feed(np.frombuffer(raw, dtype=np.int16))
        if bang is None:
            return
        now = time.time()
        if now < self._own_until or now - self._moved_at < self._MOTION_TAIL_S:
            log.debug(f"Loud sound, its own (voice or motors): {bang}")
            return
        log.info(f"Loud sound: {bang['db']} dBFS, {bang['db'] - bang['room_db']:.0f} dB over the room, "
                 f"rising {bang['rise_db']} dB at once")
        self._bus.publish(EventType.UNUSUAL_SOUND, bang, source="AudioInput")

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

        import numpy as np
        try:                         # the dedicated detector, in shadow mode (services/wakeword.py)
            from services.wakeword import WakeModel
            root = Path(__file__).resolve().parents[1]
            wake_model = WakeModel(root / "models" / "wakeword", root / "data" / "wakeword")
        except Exception as e:
            log.info(f"WakeModel off: {e}")
            wake_model = None

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(self._vosk_model_path))
        # Vosk always expects 16000 Hz
        rec = vosk.KaldiRecognizer(model, 16000)
        rec.SetWords(True)
        # The name, spotted by a recogniser that may only hear it or [unk] (WakeSpotter).
        kws = vosk.KaldiRecognizer(model, 16000, json.dumps([self._wake_word, "[unk]"]))
        kws.SetWords(True)
        self.model_loaded = True
        spotter = WakeSpotter(self._wake_word,
                              strict_fn=lambda: time.time() - self._moved_at < self._MOTION_TAIL_S)
        stream_s = 0.0

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

        # A stream that fails (a USB hiccup, the mic pulled out) is reopened: before,
        # the thread ended and spooky stayed deaf until the next restart.
        while self._active:
            try:
                with sd.RawInputStream(**stream_kwargs) as stream:
                    while self._active:
                        data, _ = stream.read(blocksize)
                        raw = bytes(data)
                        self.heard_at = time.time()
                        # Resample to 16000 Hz if needed (numpy — Python 3.13 safe)
                        if hw_rate != 16000:
                            raw = self._resample_numpy(raw, hw_rate, 16000)
                        stream_s += len(raw) / 32000.0
                        if self._moving_fn is not None and self._moving_fn():
                            self._moved_at = time.time()
                        self._listen_for_bangs(raw)
                        if wake_model is not None:
                            try:
                                wake_model.feed(np.frombuffer(raw, dtype=np.int16))
                            except Exception as e:        # never at the microphone's expense
                                log.warning(f"WakeModel: {e} — off until restart")
                                wake_model = None
                        if kws.AcceptWaveform(raw):
                            spotter.spotted(json.loads(kws.Result()).get("result", []))
                        if rec.AcceptWaveform(raw):
                            result = json.loads(rec.Result())
                            spotter.heard(result.get("result", []))
                            text   = result.get("text", "").strip()
                            if text:
                                self._handle_text(text, final=True)
                        else:
                            partial = json.loads(rec.PartialResult()).get("partial", "").strip()
                            if partial:
                                self._handle_text(partial, final=False)
                        command = spotter.poll(stream_s)
                        if command is not None:
                            self._wake(command, f"(spotted) {command}")
                        # The command ends after a silence — checked here, every half second:
                        # waiting for the next transcript to check it left a command hanging
                        # until somebody spoke again.
                        if self._state == self._STATE_ACTIVE and time.time() - self._last_speech_ts >= (
                                self._command_timeout if any(self._command_parts) else self._FIRST_WORDS_S):
                            self._emit_command()
            except Exception as e:
                if not self._active:
                    break                   # stopping: the stream was closed under the read
                log.error(f"AudioInput stream error: {e} — reopening in {self._REOPEN_S:.0f} s")
                time.sleep(self._REOPEN_S)
                try:                        # a replugged USB mic comes back as a new device
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass

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

    _OWN_TAIL_S = 1.0     # the room still echoes it, and Vosk finishes the sentence late
    _FIRST_WORDS_S = 5.0  # after a lone name, time to start the command — people pause

    def _handle_text(self, text: str, final: bool) -> None:
        own = time.time() < self._own_until
        self._bus.publish(
            EventType.SPEECH_TRANSCRIBED,
            {"text": text, "final": final, "own": own},
            source="AudioInput",
        )
        if own:
            return

        # Waking is the WakeSpotter's alone. The name's sound in the free transcript
        # (wake_match) used to wake it too: since the spotter (2026-10-23 13:40) it
        # never found a call the spotter missed, and on its own it woke on "spetta a
        # lei", "sapevano già", "spietata con un falso" — any sentence starting with
        # sp- and a k or t. It still keeps the name out of a command said again.
        if self._state == self._STATE_ACTIVE:
            if final and text:
                again = wake_match(text, self._wake_word)   # the name said again: not part of the command
                self._command_parts.append(text if again is None else again)
                self._last_speech_ts = time.time()

            # Silence timeout → emit command
            if time.time() - self._last_speech_ts >= self._command_timeout:
                self._emit_command()

    _REOPEN_S = 2.0       # after a stream error, before opening the microphone again
    _MOTION_TAIL_S = 1.5  # the name's result arrives about a second after it was said
    _REFRACTORY_S = 3.0

    def _wake(self, remainder: str, heard: str) -> None:
        """The name was heard: listen for a command, starting with what followed it."""
        now = time.time()
        if self._state != self._STATE_IDLE or now < self._own_until or now < self._refractory_until:
            return
        self._refractory_until = now + self._REFRACTORY_S
        log.info(f"Wake word detected: '{heard}'")
        self._bus.publish(EventType.WAKE_WORD_DETECTED, {"transcript": heard}, source="AudioInput")
        self._bus.publish(EventType.MIC_STATE_CHANGED, {"state": "listening"}, source="AudioInput")
        self._state = self._STATE_ACTIVE
        self._command_parts = [remainder] if remainder else []
        self._last_speech_ts = time.time()

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

    def __init__(self, bus: EventBus, cfg, moving_fn: Optional[Callable[[], bool]] = None):
        robot_cfg = cfg.get("robot", {})
        audio_cfg = cfg.get("audio", {})

        lang       = robot_cfg.get("language", "it")
        tts_method = audio_cfg.get("tts_method", "auto")
        wake_word    = audio_cfg.get("wake_word", "spooky")
        vosk_model   = audio_cfg.get("vosk_model", "~/vosk-model-it")
        samplerate   = audio_cfg.get("samplerate", 16000)
        blocksize    = audio_cfg.get("blocksize",  8000)
        device       = audio_cfg.get("mic_device", None)   # None = sounddevice default
        cmd_timeout  = audio_cfg.get("command_timeout_s", 3.0)

        bus.subscribe(EventType.WAKE_WORD_DETECTED, lambda ev: self._out.chirp())
        self._out = AudioOutput(bus, language=lang, method=tts_method,
                                speaker_pin=audio_cfg.get("speaker_enable_pin"),
                                piper_voice=audio_cfg.get("piper_voice"),
                                piper_speaker=audio_cfg.get("piper_speaker"))
        self._inp = AudioInput(
            bus,
            wake_word=wake_word,
            vosk_model_path=vosk_model,
            samplerate=samplerate,
            blocksize=blocksize,
            command_timeout_s=cmd_timeout,
            device=device,
            moving_fn=moving_fn,
        )

    def start(self) -> None:
        ok = self._inp.start()
        if not ok:
            log.warning("AudioService: STT/wake-word unavailable (TTS still active)")
        log.info("AudioService ready")

    def stop(self) -> None:
        self._inp.stop()
        log.info("AudioService stopped")

    def say(self, text: str, wait: bool = False, feeling: Optional[str] = None, continues: bool = False) -> None:
        self._out.say(text, wait=wait, feeling=feeling, continues=continues)

    _MIC_FRESH_S = 3.0      # audio arrives every half second while the stream runs

    def health(self, now: Optional[float] = None) -> dict:
        """Microphone, speech recognition and voice: each working or not (services/health.py)."""
        inp = self._inp
        age = (now or time.time()) - inp.heard_at if inp.heard_at else None
        return {
            "microphone": {"ok": age is not None and age < self._MIC_FRESH_S,
                           "detail": inp.problem or ("no audio yet" if age is None else f"audio {age:.1f} s ago")},
            "speech": {"ok": inp.model_loaded,
                       "detail": inp.problem or ("Vosk model loaded" if inp.model_loaded else "loading")},
            "voice": {"ok": self._out.method != AudioOutput.M_SILENT, "detail": self._out.method},
        }

    @property
    def volume(self) -> int:
        return self._out.volume

    def set_volume(self, volume: float) -> int:
        return self._out.set_volume(volume)

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
