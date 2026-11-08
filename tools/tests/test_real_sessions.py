"""Regression tests on recorded sessions (sessions/).  Session 5 has a Surfr phone-app
reference list; tuning must not drop below what the current detector achieves on it."""
import os, sys
import numpy as np, pandas as pd, pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from kitelog.fitio import read_fit
from kitelog.detect import build_streams, detect_jumps, DetectParams
from kitelog.height import estimate_height, HeightParams
from kitelog.session import jumps_table

SESS = os.path.join(ROOT, "sessions")
SURFR_OFFSET_S = 548.5      # Surfr start = watch first record - 548.5 s
POP_TO_SURFR_S = 2.0        # Surfr stamps a jump ~2 s after the take-off pop


def _jumps(name):
    path = os.path.join(SESS, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} not present")
    rec, _ = read_fit(path)
    s = build_streams(rec)
    cands, _ = detect_jumps(s, DetectParams())
    j = jumps_table(s, cands, [estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams()) for c in cands])
    return rec, j[j.accepted]


def test_session5_against_surfr():
    rec, acc = _jumps("2026-11-04_brazil-5.fit")
    ref = pd.read_csv(os.path.join(SESS, "2026-11-04_brazil-5_surfr.csv"), comment="#")
    off = rec.ts.iloc[0] - SURFR_OFFSET_S
    fine = ref[(ref.time_precision_s == 1) & (ref.n != 1)]          # jump 1 is before the watch started
    found, heights = 0, []
    used = set()
    for _, r in fine.iterrows():
        d = acc.ts_to - (off + r.t_since_surfr_start_s - POP_TO_SURFR_S)
        c = d[(d.abs() <= 2.5) & (~acc.index.isin(used))]
        if len(c):
            i = c.abs().idxmin(); used.add(i); found += 1; heights.append((acc.loc[i].height_m, r.height))
    extra = len(acc[acc.ts_to < off + 3600]) - found
    h = np.array(heights)
    assert found >= 19, f"recall vs Surfr dropped: {found}/{len(fine)}"
    assert extra <= 4, f"too many jumps Surfr did not see: {extra}"
    assert np.abs(h[:, 0] - h[:, 1]).mean() < 0.9, "height error vs Surfr grew"
    assert 44 <= len(acc) <= 58, f"session total {len(acc)} far from Surfr's 54"


@pytest.mark.parametrize("name,lo,hi", [("2026-11-03_brazil-1.fit", 13, 20), ("2026-11-03_brazil-4.fit", 6, 11),
                                         ("2026-11-05_brazil-6.fit", 4, 9), ("2026-11-05_brazil-7.fit", 17, 27)])
def test_other_sessions_counts(name, lo, hi):
    _, acc = _jumps(name)
    assert lo <= len(acc) <= hi


def test_no_riding_off_jump():
    """Session 7 had an 11.4 m / 6.75 s 'jump' that was a slam plus riding off (speed +9 km/h in the air)."""
    _, acc = _jumps("2026-11-05_brazil-7.fit")
    assert acc.airtime_s.max() < 6.5 and acc.height_m.max() < 10.0


@pytest.mark.parametrize("name,min_found,max_extra,max_h_mae", [
    ("2026-11-06_brazil-8", 17, 3, 1.0),     # 25 exact-time Surfr jumps
    ("2026-11-06_brazil-9", 13, 4, 1.7),     # 18 exact-time Surfr jumps, overpowered day
    ("2026-11-04_brazil-5", 18, 5, 1.0),     # 27 exact-time Surfr jumps
    ("2026-11-07_brazil-10", 4, 8, 1.0),     # 8 exact-time Surfr jumps, calm day
])
def test_against_surfr(name, min_found, max_extra, max_h_mae):
    """Detection and height vs the Surfr phone app (sessions/*_surfr.csv), via tools/surfr_compare.py."""
    import surfr_compare as SC
    fit = os.path.join(SESS, name + ".fit"); ref = os.path.join(SESS, name + "_surfr.csv")
    if not (os.path.exists(fit) and os.path.exists(ref)):
        pytest.skip("reference missing")
    sm = SC.summary(SC.compare(fit, ref))
    assert sm["found"] >= min_found, sm
    assert sm["extra"] <= max_extra, sm
    assert sm["h_mae"] <= max_h_mae, sm


@pytest.mark.parametrize("name,min_recall,min_precision", [
    ("2026-11-04_brazil-5", 0.65, 0.70), ("2026-11-06_brazil-8", 0.68, 0.88),
    ("2026-11-06_brazil-9", 0.75, 0.75), ("2026-11-07_brazil-10", 0.68, 0.60),
])
def test_whole_session_against_surfr(name, min_recall, min_precision):
    """Exact and minute-rounded Surfr jumps over the whole session (Nov 2026: 75 % recall, 80 % precision pooled)."""
    import surfr_compare as SC
    fit = os.path.join(SESS, name + ".fit"); ref = os.path.join(SESS, name + "_surfr.csv")
    if not (os.path.exists(fit) and os.path.exists(ref)):
        pytest.skip("reference missing")
    w = SC.whole_session(fit, ref)
    assert w["recall"] >= min_recall and w["precision"] >= min_precision, {k: w[k] for k in ("found", "n_ref", "n_kitelog")}
