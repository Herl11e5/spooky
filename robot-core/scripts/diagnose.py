#!/usr/bin/env python3
"""
diagnose.py — Test hardware components independently.

Usage (from robot-core dir with venv active):
    python scripts/diagnose.py [motor|audio|camera|all]

Default: all
"""
import sys
import time

def test_motor():
    print("\n── Motor ──────────────────────────────────────────")
    try:
        from picrawler import Picrawler
        c = Picrawler()
        print("  PiCrawler OK")

        # Show servo list
        if hasattr(c, 'servo_list'):
            print(f"  servo_list length: {len(c.servo_list)}")

        # Test stand
        print("  do_action('stand', 1) …", end=" ", flush=True)
        c.do_action("stand", 1)
        print("OK")
        time.sleep(0.5)

        # Test forward briefly
        print("  do_action('forward', 1, 40) …", end=" ", flush=True)
        c.do_action("forward", 1, 40)
        print("OK")
        time.sleep(0.6)

        print("  do_action('stand', 1) …", end=" ", flush=True)
        c.do_action("stand", 1)
        print("OK")

        # Test pan servo (index 12)
        for idx in [12, 13]:
            print(f"  set_angle({idx}, 20) …", end=" ", flush=True)
            try:
                c.set_angle(idx, 20)
                print("OK")
                time.sleep(0.3)
                c.set_angle(idx, 0)
            except Exception as e:
                print(f"FAILED: {e}")

        print("  Motor: PASS")
    except ImportError:
        print("  picrawler not installed — simulation only, SKIP")
    except Exception as e:
        print(f"  Motor FAIL: {e}")


def test_audio():
    print("\n── Audio (TTS) ─────────────────────────────────────")
    import subprocess, tempfile, os

    # Check espeak-ng
    try:
        subprocess.run(["espeak-ng", "--version"], capture_output=True, check=True)
        print("  espeak-ng: found")
    except Exception:
        print("  espeak-ng: NOT FOUND — install: sudo apt install espeak-ng")
        return

    # Generate WAV
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(
            ["espeak-ng", "-v", "it", "-s", "140", "-w", wav, "Ciao, sono Spooky"],
            check=True, capture_output=True, timeout=10
        )
        print("  espeak-ng synthesis: OK")
    except Exception as e:
        print(f"  espeak-ng synthesis FAIL: {e}")
        return

    # aplay
    try:
        r = subprocess.run(["aplay", "-l"], capture_output=True)
        print("  ALSA devices:")
        for line in r.stdout.decode().splitlines():
            if "card" in line.lower():
                print(f"    {line}")
    except Exception:
        pass

    for dev_args in ([], ["-D", "plug:default"], ["-D", "plughw:0,0"]):
        label = " ".join(dev_args) or "default"
        print(f"  aplay {label} …", end=" ", flush=True)
        try:
            subprocess.run(
                ["aplay", "-q"] + dev_args + [wav],
                check=True, capture_output=True, timeout=10
            )
            print("OK  ← this device works")
            break
        except subprocess.CalledProcessError as e:
            print(f"FAIL ({e.stderr.decode(errors='replace')[:60].strip()})")
    os.unlink(wav)

    print("\n── Audio (Microphone) ───────────────────────────────")
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        print("  sounddevice devices:")
        for i, d in enumerate(devs):
            tag = "<-- default input" if i == sd.default.device[0] else ""
            print(f"    [{i}] {d['name']}  in={d['max_input_channels']}  {tag}")

        default_in = sd.default.device[0]
        print(f"  Recording 1s from device [{default_in}] …", end=" ", flush=True)
        rec = sd.rec(16000, samplerate=16000, channels=1, dtype='int16')
        sd.wait()
        vol = int(abs(rec).mean())
        print(f"OK (mean amplitude={vol})")
        if vol < 10:
            print("  WARNING: very low amplitude — check microphone connection")
    except ImportError:
        print("  sounddevice not installed — pip install sounddevice")
    except Exception as e:
        print(f"  Microphone FAIL: {e}")


def test_camera():
    print("\n── Camera ──────────────────────────────────────────")
    # Try picamera2
    try:
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_preview_configuration(
            main={"format": "RGB888", "size": (320, 240)}
        ))
        cam.start()
        time.sleep(0.5)
        frame = cam.capture_array()
        cam.stop()
        cam.close()
        print(f"  picamera2: OK  frame={frame.shape}")

        # Quick face detection test
        import cv2, numpy as np
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        clf = cv2.CascadeClassifier(cascade_path)
        faces = clf.detectMultiScale(gray, 1.3, 5, minSize=(40, 40))
        print(f"  Haar face detection: {len(faces)} face(s) found in test frame")
        return
    except ImportError:
        print("  picamera2 not available, trying OpenCV…")
    except Exception as e:
        print(f"  picamera2 FAIL: {e}")

    # Try OpenCV
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            ok, frame = cap.read()
            cap.release()
            if ok:
                print(f"  OpenCV webcam: OK  frame={frame.shape}")
            else:
                print("  OpenCV webcam: opened but read() failed")
        else:
            print("  No camera found — check connections")
    except Exception as e:
        print(f"  Camera FAIL: {e}")


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["all"]
    if "all" in targets:
        targets = ["motor", "audio", "camera"]
    for t in targets:
        if t == "motor":  test_motor()
        elif t == "audio": test_audio()
        elif t == "camera": test_camera()
        else: print(f"Unknown target: {t}")
    print("\nDone.")


if __name__ == "__main__":
    main()
