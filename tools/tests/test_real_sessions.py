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


@pytest.mark.parametrize("name,lo,hi", [("2026-11-03_brazil-1.fit", 13, 20), ("2026-11-03_brazil-4.fit", 6, 11)])
def test_other_sessions_counts(name, lo, hi):
    _, acc = _jumps(name)
    assert lo <= len(acc) <= hi
