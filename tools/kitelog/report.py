"""Plots and exports.  Palette/mark rules follow the dataviz skill reference
(light surface, fixed categorical order, thin marks, recessive grid, no dual axes)."""
from __future__ import annotations

import json
import math
from dataclasses import asdict

import numpy as np
import pandas as pd

from .detect import Streams, Features, JumpCandidate
from .height import HeightResult, HeightParams
from .physics import JumpModel, pressure_to_rel_height
from .session import local_xy, KMH

C_BLUE, C_ORANGE, C_AQUA, C_YELLOW, C_MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"
C_TEXT, C_TEXT2, C_MUTED, C_GRID, C_SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e8e7e3", "#fcfcfb"


def _style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": C_SURFACE, "axes.facecolor": C_SURFACE, "savefig.facecolor": C_SURFACE,
        "axes.edgecolor": C_GRID, "axes.labelcolor": C_TEXT2, "xtick.color": C_TEXT2, "ytick.color": C_TEXT2,
        "text.color": C_TEXT, "axes.grid": True, "grid.color": C_GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 9, "axes.titlesize": 10,
        "axes.titleweight": "bold", "axes.titlelocation": "left", "lines.linewidth": 1.4,
    })
    return plt


def plot_jump(s: Streams, F: Features, c: JumpCandidate, h: HeightResult, path: str, title: str = "",
              prm: HeightParams | None = None, truth: dict | None = None):
    plt = _style()
    prm = prm or HeightParams()
    t0 = c.t_to
    w0, w1 = t0 - 6.0, c.t_land + 6.0
    sel = (s.t >= w0) & (s.t <= w1)
    tt = s.t[sel] - t0
    fig, axes = plt.subplots(4, 1, figsize=(9, 8.6), sharex=True, gridspec_kw=dict(hspace=0.35))
    # 1 accel magnitude
    ax = axes[0]
    ax.plot(tt, F.mag[sel], color=C_MUTED, lw=0.6, alpha=0.7, label="|f| 25 Hz")
    ax.plot(tt, F.m4[sel], color=C_BLUE, lw=1.6, label="|f| low-pass 4 Hz")
    ax.axhline(1.0, color=C_GRID, lw=1)
    ax.set_ylabel("specific force (g)")
    ax.set_title("Accelerometer magnitude - load peak %.1f g, flight %.2f g, impact %.1f g" % (c.takeoff_peak_g, c.flight_level_g, c.landing_peak_g))
    ax.legend(loc="upper left", frameon=False, ncol=1)
    # 2 roughness
    ax = axes[1]
    rr = np.where(np.isinf(F.rough[sel]), np.nan, F.rough[sel])
    ax.plot(tt, rr, color=C_ORANGE, lw=1.6, label="chop roughness")
    thr = np.maximum(0.30 * F.rough_ref[sel], 0.035)
    ax.plot(tt, thr, color=C_ORANGE, lw=1.0, ls="--", label="flight threshold")
    ax.set_ylabel("RMS >3 Hz (g)")
    ax.set_title("Vibration drops while airborne")
    ax.legend(loc="upper right", frameon=False, ncol=2)
    # 3 barometer
    ax = axes[2]
    psel = (s.press_t >= w0) & (s.press_t <= w1)
    pt = s.press_t[psel]
    if np.isfinite(h.baseline_pre_pa):
        p_pre = h.baseline_pre_pa
        p_post = h.baseline_post_pa if np.isfinite(h.baseline_post_pa) else p_pre
        tc0 = t0 + np.mean(prm.pre_win)
        tc1 = c.t_land + np.mean(prm.post_win)
        base = p_pre + (p_post - p_pre) * np.clip((pt - tc0) / max(tc1 - tc0, 1e-6), 0, 1)
        hb = pressure_to_rel_height(s.press[psel], base, prm.temp_c)
        ax.plot(pt - t0, hb, "o", color=C_AQUA, ms=5, label="baro height (1 Hz)")
        if np.isfinite(h.h_fit):
            jm = JumpModel.from_airtime(c.airtime, h.fit_a_up, h.fit_a_dn)
            tau = np.linspace(0, c.airtime, 200)
            ax.plot(tau + h.fit_lag_s, jm.h(tau), color=C_AQUA, lw=1.6, label="physics fit: %.1f m" % h.h_fit)
    if truth is not None and "h_profile" in truth:
        ax.plot(truth["h_profile"][0] - t0, truth["h_profile"][1], color=C_MUTED, lw=1.0, ls=":", label="true wrist height")
    ax.set_ylabel("height (m)")
    ax.set_title("Barometric height - %s = %.1f m  (airtime model %.1f m, ballistic bound %.1f m)" % (h.method, h.best, h.h_airtime, h.h_ballistic))
    ax.legend(loc="upper right", frameon=False)
    # 4 speed
    ax = axes[3]
    ax.plot(tt, F.speed[sel] * KMH, color=C_YELLOW, lw=1.6, label="GPS speed")
    ax.set_ylabel("speed (km/h)")
    ax.set_xlabel("time from take-off (s)")
    ax.set_title("GPS speed (1 Hz, interpolated)")
    for ax in axes:
        ax.axvline(0, color=C_TEXT2, lw=0.9, ls="-")
        ax.axvline(c.airtime, color=C_TEXT2, lw=0.9, ls="-")
    axes[0].annotate("take-off", (0, axes[0].get_ylim()[1]), ha="left", va="top", fontsize=8, color=C_TEXT2, xytext=(3, -2), textcoords="offset points")
    axes[0].annotate("landing", (c.airtime, axes[0].get_ylim()[1]), ha="left", va="top", fontsize=8, color=C_TEXT2, xytext=(3, -2), textcoords="offset points")
    fig.suptitle(title or "Jump - airtime %.2f s, height %.1f m" % (c.airtime, h.best), x=0.02, ha="left", fontweight="bold")
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def plot_session(s: Streams, F: Features, jumps: pd.DataFrame, path: str):
    plt = _style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), gridspec_kw=dict(width_ratios=[1.1, 1.4], wspace=0.25))
    ok = np.isfinite(s.lat) & np.isfinite(s.lon)
    if ok.sum() > 2:
        x, y = local_xy(s.lat[ok], s.lon[ok])
        ax1.plot(x, y, color=C_BLUE, lw=1.0, label="track")
        acc = jumps[jumps["accepted"]] if len(jumps) else jumps
        if len(acc):
            tp = s.gps_t[ok]
            jx = np.interp(acc["t_to"], tp, x)
            jy = np.interp(acc["t_to"], tp, y)
            ax1.scatter(jx, jy, s=20 + 8 * acc["height_m"].fillna(0), color=C_ORANGE, zorder=3,
                        edgecolor=C_SURFACE, linewidth=1, label="jumps (size = height)")
        ax1.set_aspect("equal")
        ax1.set_xlabel("east (m)")
        ax1.set_ylabel("north (m)")
        ax1.legend(loc="upper left", frameon=False)
    ax1.set_title("GPS track")
    tmin = s.t[0]
    gok = np.isfinite(s.speed)
    ax2.plot((s.gps_t[gok] - tmin) / 60, s.speed[gok] * KMH, color=C_YELLOW, lw=0.9, label="speed (km/h)")
    acc = jumps[jumps["accepted"]] if len(jumps) else jumps
    if len(acc):
        ax2.vlines((acc["t_to"] - tmin) / 60, 0, 1, transform=ax2.get_xaxis_transform(), color=C_ORANGE, lw=0.8, alpha=0.7)
        top = acc.nlargest(5, "height_m")                      # label only the 5 biggest: no label collisions
        for _, j in top.iterrows():
            ax2.annotate("%.1f m" % j["height_m"], ((j["t_to"] - tmin) / 60, 1.0), xycoords=("data", "axes fraction"),
                         fontsize=8, color=C_TEXT2, ha="center", va="bottom")
    ax2.set_xlabel("session time (min)")
    ax2.set_ylabel("speed (km/h)")
    ax2.set_title("Speed and jumps")
    ax2.legend(loc="upper right", frameon=False)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def export_gpx(s: Streams, path: str, name="KiteLog session"):
    import datetime as dt
    ok = np.isfinite(s.lat) & np.isfinite(s.lon)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<gpx version="1.1" creator="kitelog" xmlns="http://www.topografix.com/GPX/1/1">',
             f"<trk><name>{name}</name><trkseg>"]
    for la, lo, t in zip(s.lat[ok], s.lon[ok], s.gps_t[ok] + s.ts_offset):
        iso = dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f'<trkpt lat="{la:.7f}" lon="{lo:.7f}"><time>{iso}</time></trkpt>')
    lines += ["</trkseg></trk></gpx>"]
    open(path, "w").write("\n".join(lines))


def export_map_html(s: Streams, jumps: pd.DataFrame, path: str, title="KiteLog session"):
    ok = np.isfinite(s.lat) & np.isfinite(s.lon)
    pts = [[round(float(a), 6), round(float(b), 6)] for a, b in zip(s.lat[ok], s.lon[ok])]
    acc = jumps[jumps["accepted"]] if len(jumps) else jumps
    markers = []
    if len(acc) and ok.sum() > 2:
        tp = s.gps_t[ok]
        for _, j in acc.iterrows():
            la = float(np.interp(j["t_to"], tp, s.lat[ok]))
            lo = float(np.interp(j["t_to"], tp, s.lon[ok]))
            markers.append(dict(lat=la, lon=lo, h=float(j["height_m"]), air=float(j["airtime_s"]), d=float(j["distance_m"]), n=int(j["jump"])))
    html = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body,#map{{height:100%;margin:0}} .lg{{font:12px sans-serif;background:#fcfcfb;padding:6px 8px;border-radius:4px}}</style></head>
<body><div id="map"></div><script>
const pts={json.dumps(pts)}; const jumps={json.dumps(markers)};
const map=L.map('map'); L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}}).addTo(map);
const line=L.polyline(pts,{{color:'#2a78d6',weight:2}}).addTo(map); map.fitBounds(line.getBounds());
jumps.forEach(j=>L.circleMarker([j.lat,j.lon],{{radius:4+Math.sqrt(Math.max(j.h,0))*2,color:'#eb6834',fillColor:'#eb6834',fillOpacity:0.6,weight:1}})
 .bindPopup(`<b>Jump ${{j.n}}</b><br>height ${{j.h.toFixed(1)}} m<br>airtime ${{j.air.toFixed(2)}} s<br>distance ${{j.d.toFixed(0)}} m`).addTo(map));
</script></body></html>"""
    open(path, "w").write(html)
