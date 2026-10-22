#!/usr/bin/env python3
"""Monte-Carlo validation of the offline pipeline against simulator ground truth."""
import argparse, os, json
import numpy as np, pandas as pd
from kitelog.simulate import simulate, SimConfig
from kitelog.detect import build_streams, detect_jumps, DetectParams
from kitelog.height import estimate_height, HeightParams
from kitelog.session import jump_distance, baro_consistent

SCENARIOS = {
    "baseline":     dict(),
    "glassy":       dict(chop=0.3),
    "rough":        dict(chop=2.0),
    "noisy_baro":   dict(baro_noise_pa=12.0),
    "wind_port":    dict(dyn_coupling=0.4),
    "splashy":      dict(splash_rate_per_h=40.0),
    "dropouts":     dict(record_drop_prob=0.02, gps_dropout_prob=0.10),
}


def run(scenario, n_sessions, duration, seed0):
    rows, fp_rows, miss = [], [], 0
    for k in range(n_sessions):
        cfg = SimConfig(duration_s=duration, seed=seed0 + k, **SCENARIOS[scenario])
        rec, truth, _ = simulate(cfg)
        s = build_streams(rec)
        cands, F = detect_jumps(s, DetectParams())
        off = truth["tmr_offset_s"]
        used = set()
        for j in truth["jumps"]:
            m = [i for i, c in enumerate(cands) if c.t_to < j["t_land"] + off and c.t_land > j["t_to"] + off]
            if m and cands[m[0]].accepted:
                hh = estimate_height(cands[m[0]].t_to, cands[m[0]].t_land, s.press_t, s.press, HeightParams())
                if not baro_consistent(cands[m[0]], hh):
                    cands[m[0]].accepted = False
                    cands[m[0]].reject_reason = "baro_inconsistent"
            if not m or not cands[m[0]].accepted:
                miss += 1
                rows.append(dict(scenario=scenario, session=k, h_true=j["h_max"], airtime_true=j["airtime"], detected=False,
                                 miss_reason=("no_candidate" if not m else cands[m[0]].reject_reason)))
                continue
            i = m[0]; used.add(i); c = cands[i]
            h = estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams())
            d = jump_distance(s, c.t_to, c.t_land)
            rows.append(dict(scenario=scenario, session=k, detected=True, h_true=j["h_max"], airtime_true=j["airtime"],
                             dist_true=j["distance"], v_to_true=j["v_to"],
                             airtime=c.airtime, t_to_err=c.t_to - (j["t_to"] + off), t_land_err=c.t_land - (j["t_land"] + off), flags=c.flags,
                             h_best=h.best, method=h.method, h_fit=h.h_fit, h_quad=h.h_quad, h_max_sample=h.h_max_sample,
                             h_airtime=h.h_airtime, n_baro=h.n_flight_samples,
                             dist_speed=d["dist_speed"], dist_pos=d["dist_pos"], v_to=d["v_to"]))
        fp = [c for i, c in enumerate(cands) if i not in used and c.accepted
              and baro_consistent(c, estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams()))]
        fp_rows.append(dict(scenario=scenario, session=k, false_positives=len(fp), n_true=len(truth["jumps"]),
                            riding_min=duration / 60))
    return pd.DataFrame(rows), pd.DataFrame(fp_rows)


def summarise(df, fp):
    out = []
    for sc, g in df.groupby("scenario"):
        det = g[g.detected]
        f = fp[fp.scenario == sc]
        rec = dict(scenario=sc, jumps=len(g), recall=det.shape[0] / max(len(g), 1),
                   false_pos_per_h=f.false_positives.sum() / (f.riding_min.sum() / 60),
                   airtime_bias=(det.airtime - det.airtime_true).mean(), airtime_mae=(det.airtime - det.airtime_true).abs().mean(),
                   t_to_bias=det.t_to_err.mean(), t_land_bias=det.t_land_err.mean())
        for est in ["h_best", "h_fit", "h_quad", "h_max_sample", "h_airtime"]:
            e = det[est] - det.h_true
            rec[est + "_bias"] = e.mean(); rec[est + "_mae"] = e.abs().mean()
            rec[est + "_mape"] = (e.abs() / det.h_true).mean() * 100
        for est in ["dist_speed", "dist_pos"]:
            e = det[est] - det.dist_true
            rec[est + "_mae"] = e.abs().mean()
        rec["baro_fit_used_%"] = (det.method == "baro_fit").mean() * 100
        out.append(rec)
    return pd.DataFrame(out)


def by_height_bin(df):
    det = df[df.detected].copy()
    det["bin"] = pd.cut(det.h_true, [0, 1, 2, 5, 10, 25], labels=["<1 m", "1-2 m", "2-5 m", "5-10 m", ">10 m"])
    g = det.groupby("bin", observed=True)
    out = pd.DataFrame(dict(
        n=g.size(),
        h_best_bias=g.apply(lambda x: (x.h_best - x.h_true).mean()),
        h_best_mae=g.apply(lambda x: (x.h_best - x.h_true).abs().mean()),
        h_best_mape=g.apply(lambda x: ((x.h_best - x.h_true).abs() / x.h_true).mean() * 100),
        h_best_p90_abs=g.apply(lambda x: (x.h_best - x.h_true).abs().quantile(0.9)),
        h_airtime_mape=g.apply(lambda x: ((x.h_airtime - x.h_true).abs() / x.h_true).mean() * 100),
        airtime_mae=g.apply(lambda x: (x.airtime - x.airtime_true).abs().mean()),
        dist_speed_mae=g.apply(lambda x: (x.dist_speed - x.dist_true).abs().mean()),
    ))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=6)
    ap.add_argument("--duration", type=float, default=1200)
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--out", default="out/validation")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    dfs, fps = [], []
    for i, sc in enumerate(a.scenarios.split(",")):
        d, f = run(sc, a.sessions, a.duration, seed0=100 * (i + 1))
        dfs.append(d); fps.append(f)
        missed = d[~d.detected]
        print(f"{sc:12s} jumps={len(d)} detected={int(d.detected.sum())} fp={int(f.false_positives.sum())}"
              + (f"  missed: {missed.miss_reason.value_counts().to_dict()} heights={np.round(missed.h_true.values, 1).tolist()}" if len(missed) else ""))
    df, fp = pd.concat(dfs), pd.concat(fps)
    df.to_csv(os.path.join(a.out, "jumps_all.csv"), index=False)
    summ = summarise(df, fp)
    summ.to_csv(os.path.join(a.out, "summary_by_scenario.csv"), index=False)
    bins = by_height_bin(df[df.scenario == "baseline"]) if "baseline" in a.scenarios else by_height_bin(df)
    bins.to_csv(os.path.join(a.out, "summary_by_height_bin.csv"))
    with pd.option_context("display.width", 250, "display.max_columns", 50, "display.float_format", "{:.2f}".format):
        print("\n== by scenario ==")
        print(summ[["scenario", "jumps", "recall", "false_pos_per_h", "airtime_bias", "airtime_mae", "t_to_bias", "t_land_bias", "h_best_bias", "h_best_mae", "h_best_mape",
                    "h_fit_mape", "h_quad_mape", "h_max_sample_mape", "h_airtime_mape", "dist_speed_mae", "dist_pos_mae", "baro_fit_used_%"]].to_string(index=False))
        print("\n== baseline by true height ==")
        print(bins.to_string())
