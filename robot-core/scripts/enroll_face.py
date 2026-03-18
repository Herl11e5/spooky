#!/usr/bin/env python3
"""
enroll_face.py — CLI per la gestione dei volti riconosciuti.

Uso:
  python enroll_face.py --name "Marco" --id "marco"   # registra volto
  python enroll_face.py --list                         # mostra persone
  python enroll_face.py --delete --id "marco"          # elimina persona
  python enroll_face.py --info --id "marco"            # dettagli persona

La registrazione apre la camera e cattura 15 campioni del volto.
Guarda direttamente la camera e muoviti leggermente per variare l'angolo.
"""

import argparse
import sys
import time
from pathlib import Path

# Add robot-core to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.config import load_config
from services.vision import FaceDatabase, FaceDetector, FaceRecognizer, CameraBackend


def cmd_enroll(args, db: FaceDatabase, det: FaceDetector, rec: FaceRecognizer, cfg) -> None:
    if not args.name or not args.id:
        print("Errore: --name e --id sono obbligatori per la registrazione")
        sys.exit(1)

    print(f"\n📷  Registrazione di '{args.name}' (id: {args.id})")
    print(f"   Campioni richiesti: {args.samples}")
    print("   Guarda la camera. Muoviti leggermente per variare l'angolo.")
    print("   Premi Ctrl+C per annullare.\n")

    cam_cfg = cfg.get("camera", {})
    camera = CameraBackend(
        width  = cam_cfg.get("width",  640),
        height = cam_cfg.get("height", 480),
    )
    if not camera.open():
        print("❌  Camera non disponibile")
        sys.exit(1)

    import cv2
    import numpy as np

    crops: list = []
    deadline = time.time() + float(args.timeout)

    try:
        print(f"[{'░' * args.samples}] Inizio...", end="\r", flush=True)
        while len(crops) < args.samples and time.time() < deadline:
            frame = camera.read_rgb()
            if frame is None:
                time.sleep(0.1)
                continue
            faces = det.detect(frame)
            if faces:
                x, y, w, h = faces[0]
                crop = frame[y:y+h, x:x+w]
                if crop.size > 0:
                    crops.append(crop)
                    bar = "█" * len(crops) + "░" * (args.samples - len(crops))
                    print(f"[{bar}] {len(crops)}/{args.samples}", end="\r", flush=True)
            time.sleep(0.12)
    except KeyboardInterrupt:
        print("\n\n⚠️  Annullato.")
        sys.exit(0)
    finally:
        camera.close()

    print()
    if len(crops) < args.samples // 2:
        print(f"❌  Solo {len(crops)} campioni catturati — troppo pochi. Riprova.")
        sys.exit(1)

    print(f"⏳  Elaborazione {len(crops)} campioni...")
    ok = db.enroll_person(args.id, args.name, crops, rec)
    if ok:
        print(f"✅  '{args.name}' registrato con successo!")
        print(f"   Per verificare: python enroll_face.py --list")
    else:
        print("❌  Registrazione fallita")
        sys.exit(1)


def cmd_list(db: FaceDatabase) -> None:
    persons = db.all_persons()
    if not persons:
        print("Nessuna persona registrata.")
        return
    print(f"\n{'ID':<20} {'Nome':<30}")
    print("─" * 50)
    for pid, name in persons.items():
        print(f"{pid:<20} {name:<30}")
    print(f"\nTotale: {len(persons)} persona/e")


def cmd_delete(args, db: FaceDatabase) -> None:
    if not args.id:
        print("Errore: --id è obbligatorio per eliminare")
        sys.exit(1)
    persons = db.all_persons()
    if args.id not in persons:
        print(f"Persona '{args.id}' non trovata.")
        sys.exit(1)
    name = persons[args.id]
    confirm = input(f"Eliminare '{name}' ({args.id})? [s/N] ")
    if confirm.lower() in ("s", "si", "sì", "y", "yes"):
        db.delete_person(args.id)
        print(f"✅  '{name}' eliminato.")
    else:
        print("Annullato.")


def cmd_info(args, db: FaceDatabase) -> None:
    if not args.id:
        print("Errore: --id è obbligatorio")
        sys.exit(1)
    persons = db.all_persons()
    if args.id not in persons:
        print(f"Persona '{args.id}' non trovata.")
        sys.exit(1)
    name = persons[args.id]
    person_dir = db._root / args.id
    samples = list((person_dir / "samples").glob("*.jpg")) if (person_dir / "samples").exists() else []
    has_model = (person_dir / "lbph_model.xml").exists()
    print(f"\nID:        {args.id}")
    print(f"Nome:      {name}")
    print(f"Campioni:  {len(samples)}")
    print(f"Modello:   {'presente' if has_model else 'mancante'}")
    print(f"Percorso:  {person_dir}")


def main():
    parser = argparse.ArgumentParser(description="Gestione volti Spooky")
    parser.add_argument("--name",    help="Nome visualizzato")
    parser.add_argument("--id",      help="ID univoco persona (snake_case)")
    parser.add_argument("--samples", type=int, default=15, help="Campioni da catturare (default 15)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout secondi (default 30)")
    parser.add_argument("--list",    action="store_true", help="Mostra persone registrate")
    parser.add_argument("--delete",  action="store_true", help="Elimina persona")
    parser.add_argument("--info",    action="store_true", help="Dettagli persona")
    parser.add_argument("--config",  default="config/robot.yaml", help="Config file")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config, ROOT / "config/local.yaml")
    face_db_path = ROOT / cfg.get("face.db_path", "data/faces/")
    db  = FaceDatabase(face_db_path)
    det = FaceDetector()
    rec = FaceRecognizer()

    # Load existing models
    db.load_all_embeddings(rec)

    if args.list:
        cmd_list(db)
    elif args.delete:
        cmd_delete(args, db)
    elif args.info:
        cmd_info(args, db)
    else:
        cmd_enroll(args, db, det, rec, cfg)


if __name__ == "__main__":
    main()
