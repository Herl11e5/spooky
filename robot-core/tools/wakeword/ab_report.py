"""
The three wake detectors side by side, from the robot's log (run on the Mac).

    python ab_report.py [since]      e.g. "2026-10-26 13:55" (default: today 00:00)

The grammar spotter decides today (its wakes: "Wake word detected"); the model in use
and each candidate only log ("would wake"). Events closer than 5 s are the same
moment. A moment two or three detectors agree on is most likely a real call; one only
a single detector saw is most likely a false wake — the table says which.
"""

import re
import subprocess
import sys
from collections import defaultdict

since = sys.argv[1] if len(sys.argv) > 1 else "today"
log = subprocess.run(["ssh", "spooky", f"journalctl -u spooky-brain --since '{since}' --no-pager -o short-unix"],
                     capture_output=True, text=True).stdout

lines = [l for l in log.splitlines() if l[:1].isdigit()]   # not the journal's "-- ... --" separators
events = []                       # (time, detector, detail)
for line in lines:
    t = float(line.split()[0])
    if "Wake word detected" in line:
        events.append((t, "vosk", line.split("Wake word detected: ", 1)[1][:60]))
    elif "WakeModel (shadow): would wake" in line or "Name heard by the model" in line:
        events.append((t, "model", re.search(r"score ([\d.]+)", line).group(1)))
    elif "WakeModel candidate" in line:
        name = re.search(r"candidate (\S+):", line).group(1)
        events.append((t, f"cand:{name}", re.search(r"score ([\d.]+)", line).group(1)))
events.sort()

moments = []                      # merge detections within 5 s into one moment
for t, who, detail in events:
    if moments and t - moments[-1]["end"] < 5:
        moments[-1]["who"][who] = detail
        moments[-1]["end"] = t
    else:
        moments.append({"start": t, "end": t, "who": {who: detail}})

detectors = sorted({who for _, who, _ in events})
alone = defaultdict(int)
shared = defaultdict(int)
for m in moments:
    for who in m["who"]:
        (shared if len(m["who"]) > 1 else alone)[who] += 1

hours = (float(lines[-1].split()[0]) - float(lines[0].split()[0])) / 3600 if lines else 0
print(f"{len(moments)} moments over {hours:.1f} h of log\n")
for m in moments:
    when = subprocess.run(["date", "-r", str(int(m["start"])), "+%H:%M:%S"], capture_output=True, text=True).stdout.strip()
    print(f"  {when}  " + "  ".join(f"{who}={m['who'][who]}" for who in detectors if who in m["who"]))
print()
for who in detectors:
    print(f"{who:14} alone {alone[who]:3} ({alone[who] / max(hours, 1e-9):.1f}/h) · with others {shared[who]:3}")
