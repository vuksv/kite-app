#!/usr/bin/env python3
"""GitHub Actions helper: analyse every FIT/ZIP attached to an issue (or comment) body and
write a Markdown summary.  usage: issue_analyze.py <body.txt> <out_dir>"""
import json, os, re, subprocess, sys, zipfile, gzip, io, urllib.request, hashlib
import pandas as pd

body_path, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
body = open(body_path, encoding="utf-8", errors="replace").read()
urls = re.findall(r"https://github\.com/user-attachments/files/[^\s)\]\"']+|https://github\.com/[^\s)\]\"']+/files/\d+/[^\s)\]\"']+", body)
urls = list(dict.fromkeys(urls))
md = []
if not urls:
    md.append("No `.fit` / `.zip` attachment found in this issue. Attach the Garmin Connect *Export Original* zip (or the .fit) and post it again.")
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from kitelog.fitio import read_fit
from kitelog.detect import build_streams, detect_jumps, DetectParams
from kitelog.height import estimate_height, HeightParams
from kitelog.session import jumps_table, session_stats
from kitelog import report

for url in urls:
    name = url.rstrip("/").split("/")[-1]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kitelog-actions"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if data[:4] == b"PK\x03\x04":
            z = zipfile.ZipFile(io.BytesIO(data)); names = [n for n in z.namelist() if n.lower().endswith(".fit")] or z.namelist()
            data = z.read(names[0]); name = os.path.basename(names[0])
        elif data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if data[8:12] != b".FIT":
            md.append(f"**{name}**: not a FIT file (first bytes `{data[:12]!r}`)."); continue
        sid = re.sub(r"[^A-Za-z0-9_.-]", "_", name).rsplit(".", 1)[0] + "_" + hashlib.sha1(data).hexdigest()[:6]
        d = os.path.join(out, sid); os.makedirs(d, exist_ok=True)
        fit_path = os.path.join(d, "activity.fit"); open(fit_path, "wb").write(data)
        rec, info = read_fit(fit_path)
        s = build_streams(rec)
        cands, F = detect_jumps(s, DetectParams())
        heights = [estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams()) for c in cands]
        jumps = jumps_table(s, cands, heights)
        stats = session_stats(s, jumps)
        jumps.to_csv(os.path.join(d, "jumps.csv"), index=False)
        report.plot_session(s, F, jumps, os.path.join(d, "session.png"))
        acc = jumps[jumps.accepted]
        for k, (c, h) in enumerate(zip(cands, heights)):
            if c.accepted: report.plot_jump(s, F, c, h, os.path.join(d, f"jump_{k+1:02d}.png"))
        md.append(f"### {name}\n")
        md.append(f"- records {info['n_records']}, batches {stats.n_batches}, duplicated {stats.n_dup_batches}, gaps {stats.n_gaps}, sport `{info['sport']}`")
        md.append(f"- duration {stats.duration_s/60:.0f} min, riding {stats.riding_time_s/60:.0f} min, distance {stats.distance_m/1000:.1f} km, max speed {stats.max_speed_kmh:.1f} km/h, tacks {stats.tacks}")
        md.append(f"- **{stats.jumps} jumps** (of {len(jumps)} candidates), max height {stats.max_height_m:.1f} m, max airtime {stats.max_airtime_s:.2f} s, longest {stats.longest_jump_m:.0f} m\n")
        if len(acc):
            md.append("| # | time (UTC) | height m | method | airtime s | length m | take-off km/h | peaks g | flags |")
            md.append("|---|---|---|---|---|---|---|---|---|")
            for _, j in acc.iterrows():
                t = pd.to_datetime(j.ts_to, unit="s").strftime("%H:%M:%S")
                md.append(f"| {int(j.jump)} | {t} | {j.height_m:.1f} | {j.height_method} | {j.airtime_s:.2f} | {j.distance_m:.0f} | {j.takeoff_speed_kmh:.1f} | {j.takeoff_peak_g:.1f}/{j.landing_peak_g:.1f} | {j['flags'] if isinstance(j['flags'], str) else ''} |")
        rej = jumps[~jumps.accepted]
        if len(rej):
            md.append(f"\n<details><summary>{len(rej)} rejected candidates</summary>\n\n| airtime s | baro m | reason |\n|---|---|---|")
            for _, j in rej.iterrows():
                md.append(f"| {j.airtime_s:.2f} | {j.height_m:.1f} ({j.height_method}) | {j.reject_reason} |")
            md.append("\n</details>")
    except Exception as e:  # keep going for other attachments
        md.append(f"**{name}**: failed - `{type(e).__name__}: {e}`")

md.append("\nFull plots and `jumps.csv` are in the workflow run's artifact (Actions tab). Open the same file in https://vuksv.github.io/kite-app/ for the interactive report.")
open(os.path.join(out, "comment.md"), "w").write("\n".join(md))
print("\n".join(md))
