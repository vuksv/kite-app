#!/usr/bin/env python3
"""Add a recorded session (.fit or Garmin Export Original .zip) to sessions/ and sessions.json.
usage: add_session.py <file> [label]   -> sessions/<YYYY-MM-DD>_<label>.fit + index entry"""
import datetime as dt, io, json, os, re, sys, zipfile
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(root, "tools"))
from kitelog.fitio import read_fit
src, label = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "session")
data = open(src, "rb").read()
if data[:4] == b"PK\x03\x04":
    z = zipfile.ZipFile(io.BytesIO(data)); n = [x for x in z.namelist() if x.lower().endswith(".fit")][0]; data = z.read(n)
tmp = os.path.join(root, "sessions", "_tmp.fit"); open(tmp, "wb").write(data)
rec, info = read_fit(tmp); os.unlink(tmp)
start = dt.datetime.fromtimestamp(float(rec["ts"].iloc[0]), dt.timezone.utc)
base = start.strftime("%Y-%m-%d") + "_" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
name, k = base + ".fit", 2
while os.path.exists(os.path.join(root, "sessions", name)):
    name = f"{base}-{k}.fit"; k += 1
open(os.path.join(root, "sessions", name), "wb").write(data)
idx_path = os.path.join(root, "sessions", "sessions.json")
idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
idx.append(dict(file=name, start_utc=start.isoformat(), records=int(info["n_records"]), label=label))
idx.sort(key=lambda e: e["start_utc"], reverse=True)
json.dump(idx, open(idx_path, "w"), indent=1)
print(name)
