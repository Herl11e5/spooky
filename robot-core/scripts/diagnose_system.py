#!/usr/bin/env python3
"""
scripts/diagnose.py — Diagnostica completa della configurazione Spooky

Verifiche:
  1. Python version
  2. Pacchetti pip installati
  3. Ollama online e modelli disponibili
  4. Configurazione YAML valida
  5. Cartelle dati accessibili
  6. Parametri hardware (GPIO su RPi)
  7. Modelli face recognition
"""

import sys
import os
import json
from pathlib import Path

# Path setup
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

def check_python():
    """Verifica Python 3.11+"""
    print("\n📌 Python")
    major, minor = sys.version_info[:2]
    version_str = f"{major}.{minor}"
    
    if (major, minor) >= (3, 11):
        print(f"  ✅ Python {version_str}")
        return True
    else:
        print(f"  ❌ Python {version_str} — richiesta 3.11+")
        return False

def check_packages():
    """Verifica pacchetti pip"""
    print("\n📦 Pacchetti Python")
    
    required = [
        'pyyaml',
        'numpy',
        'cv2',            # opencv-contrib-python
        'ollama',
        'vosk',
        'sounddevice',
        'flask',
    ]
    
    all_ok = True
    for pkg in required:
        try:
            if pkg == 'cv2':
                import cv2
                print(f"  ✅ {pkg} ({cv2.__version__})")
            elif pkg == 'ollama':
                import ollama
                print(f"  ✅ {pkg}")
            else:
                __import__(pkg)
                print(f"  ✅ {pkg}")
        except ImportError:
            print(f"  ❌ {pkg} — non installato (pip install {pkg})")
            all_ok = False
    
    return all_ok

def check_ollama():
    """Verifica Ollama online e modelli"""
    print("\n🤖 Ollama")
    
    try:
        import ollama
        import urllib.request
        
        # Test connessione
        try:
            urllib.request.urlopen('http://localhost:11434', timeout=2)
            print(f"  ✅ Ollama server online (http://localhost:11434)")
        except Exception as e:
            print(f"  ❌ Ollama server offline — esegui: ollama serve")
            print(f"     Dettaglio: {e}")
            return False
        
        # Lista modelli
        try:
            models_list = ollama.list().models
            models = [m.model for m in models_list]
            
            required_models = ['llama3.2:3b', 'moondream']
            for model in required_models:
                found = any(model in m for m in models)
                if found:
                    # Trova la versione esatta
                    exact = next((m for m in models if model in m), model)
                    print(f"  ✅ {exact}")
                else:
                    print(f"  ❌ {model} — scarica con: ollama pull {model}")
            
            if not all(any(rm in m for m in models) for rm in required_models):
                return False
            
            return True
        except Exception as e:
            print(f"  ❌ Errore listing modelli: {e}")
            return False
    
    except ImportError:
        print(f"  ❌ ollama non installato — pip install ollama")
        return False

def check_config():
    """Verifica configurazione YAML"""
    print("\n⚙️  Configurazione")
    
    try:
        from core.config import load_config
        
        config_file = ROOT / "config" / "robot.yaml"
        if not config_file.exists():
            print(f"  ❌ {config_file} non trovato")
            return False
        
        try:
            cfg = load_config(config_file)
            print(f"  ✅ robot.yaml valido")
            
            # Controlla parametri critici
            robot_name = cfg.get("robot.name", "?")
            llm_models = cfg.get("llm.model_priority", [])
            vision_models = cfg.get("vision_llm.model_priority", [])
            
            print(f"     Robot: {robot_name}")
            print(f"     LLM text: {llm_models[:1]}")
            print(f"     LLM vision: {vision_models[:1]}")
            
            # Controlla local.yaml
            local_config_file = ROOT / "config" / "local.yaml"
            if local_config_file.exists():
                print(f"  ✅ local.yaml presente (override attivo)")
            else:
                example = ROOT / "config" / "local.yaml.example"
                if example.exists():
                    print(f"  ⚠️  local.yaml non trovato — copia da local.yaml.example")
                else:
                    print(f"  ⚠️  local.yaml non trovato (usa valori di default)")
            
            return True
        except Exception as e:
            print(f"  ❌ Errore parsing YAML: {e}")
            return False
    
    except ImportError as e:
        print(f"  ❌ Configurazione non importabile: {e}")
        return False

def check_data_dirs():
    """Verifica cartelle dati"""
    print("\n📁 Cartelle Dati")
    
    required_dirs = [
        ("logs", "Log file"),
        ("data", "Database memoria"),
        ("data/faces", "Volti iscritti"),
        ("data/snapshots", "Screenshot debug"),
    ]
    
    all_ok = True
    for dir_name, desc in required_dirs:
        dir_path = ROOT / dir_name
        if dir_path.exists() and dir_path.is_dir():
            print(f"  ✅ {dir_name}/ ({desc})")
        else:
            print(f"  ❌ {dir_name}/ manca — crea con: mkdir -p {dir_path}")
            all_ok = False
    
    return all_ok

def check_rpi_hardware():
    """Verifica hardware RPi (GPIO, ecc.)"""
    print("\n🦾 Hardware RPi")
    
    is_rpi = False
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read().strip()
            if 'Raspberry Pi' in model:
                is_rpi = True
                print(f"  ✅ {model}")
    except:
        pass
    
    if not is_rpi:
        print(f"  ℹ️  Non è Raspberry Pi (development environment — OK)")
        return True
    
    # Verifica GPIO su RPi
    try:
        import gpiod
        print(f"  ✅ gpiod (libgpiod) disponibile")
    except ImportError:
        print(f"  ⚠️  gpiod non trovato — robot-hat potrebbe non funzionare")
        print(f"     Su RPi: sudo apt install python3-gpiod libgpiod2")
    
    try:
        import robot_hat
        print(f"  ✅ robot-hat importabile")
    except ImportError:
        print(f"  ⚠️  robot-hat non importabile")
        print(f"     Su RPi: pip install robot-hat")
    
    # Verifica I2C
    try:
        import smbus2
        bus = smbus2.SMBus(1)
        bus.close()
        print(f"  ✅ I2C disponibile")
    except:
        print(f"  ⚠️  I2C non disponibile — servi di abilitate: raspi-config → Interface → I2C")
    
    return True

def check_faces_db():
    """Verifica database volti"""
    print("\n👤 Database Volti")
    
    try:
        from services.vision import FaceDatabase
        
        db_path = ROOT / "data" / "faces"
        db = FaceDatabase(db_path)
        
        persons = db.all_persons()
        if persons:
            print(f"  ✅ {len(persons)} volti iscritti:")
            for pid, name in list(persons.items())[:5]:
                print(f"     - {name}")
            if len(persons) > 5:
                print(f"     ... e {len(persons) - 5} altri")
        else:
            print(f"  ⚠️  Nessun volto iscritto")
            print(f"     Iscrivi: python scripts/enroll_face.py")
        
        return True
    except Exception as e:
        print(f"  ⚠️  Errore database volti: {e}")
        return True  # Non blocca il diagnose

def check_camera():
    """Verifica camera"""
    print("\n📷 Camera")
    
    try:
        from services.vision import CameraBackend
        
        cam = CameraBackend(640, 480, 30)
        ok = cam.open()
        
        if ok:
            print(f"  ✅ Camera: {cam.backend}")
            frame = cam.read_rgb()
            if frame is not None:
                print(f"     Risoluzione: {frame.shape[1]}×{frame.shape[0]}")
            cam.close()
        else:
            print(f"  ⚠️  Camera fallback a simulazione (no hardware — OK per dev)")
        
        return True
    except Exception as e:
        print(f"  ⚠️  Errore camera: {e}")
        return True

def main():
    """Esegui tutti i check"""
    print("════════════════════════════════════════════════════════")
    print(" 🕷️  Spooky — Diagnostica")
    print("════════════════════════════════════════════════════════")
    
    checks = [
        ("Python", check_python),
        ("Packages", check_packages),
        ("Ollama", check_ollama),
        ("Config", check_config),
        ("Cartelle", check_data_dirs),
        ("RPi Hardware", check_rpi_hardware),
        ("Volti", check_faces_db),
        ("Camera", check_camera),
    ]
    
    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n  ❌ Errore in {name}: {e}")
            results.append((name, False))
    
    # Summary
    print("\n" + "=" * 56)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    
    if passed == total:
        print(f"✅ Tutti i check passati ({passed}/{total})")
        print("\nSpooky è pronto! Avvia con:")
        print("  cd robot-core")
        print("  python main.py --debug")
    else:
        print(f"⚠️  {passed}/{total} check passati")
        print("\nRisolvi gli errori sopra e riprova.")
    
    print("=" * 56)
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
