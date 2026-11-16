"""Regression tests on recorded sessions (sessions/).  Four days were recorded in parallel with the
Surfr phone app (sessions/*_surfr.csv); tools/surfr_compare.py does the comparison.  The numbers
asserted here sit a little below what the detector achieves, so that tuning cannot quietly lose it."""
import os, sys
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import surfr_compare as SC

SESS = os.path.join(ROOT, "sessions")
SURFR_DAYS = ["2026-11-04_brazil-5", "2026-11-06_brazil-8", "2026-11-06_brazil-9", "2026-11-07_brazil-10"]
_cache = {}


def _compare(name):
    fit, ref = os.path.join(SESS, name + ".fit"), os.path.join(SESS, name + "_surfr.csv")
    if not (os.path.exists(fit) and os.path.exists(ref)):
        pytest.skip(f"{name} not present")
    if name not in _cache:
        _cache[name] = SC.compare(fit, ref)
    return _cache[name]


def _jumps(name):
    path = os.path.join(SESS, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} not present")
    j = SC.load(path)[3]
    return j[j.accepted]


# Nov 2026, jumps >= 3 m: found 88 / 92 / 100 / 85 %, confirmed 95 / 97 / 89 / 89 %
@pytest.mark.parametrize("name,min_found,min_confirmed", [
    ("2026-11-04_brazil-5", 0.84, 0.90), ("2026-11-06_brazil-8", 0.88, 0.92),
    ("2026-11-06_brazil-9", 0.93, 0.83), ("2026-11-07_brazil-10", 0.80, 0.80),
])
def test_big_jumps_against_surfr(name, min_found, min_confirmed):
    r = _compare(name); sc = SC.scores(r)
    assert sc["big_found"] >= min_found, {k: r[k] for k in SC.COUNT_KEYS}
    assert sc["big_confirmed"] >= min_confirmed, {k: r[k] for k in SC.COUNT_KEYS}


def test_pooled_against_surfr():
    """All four days together: jumps >= 3 m found 90 %, confirmed 94 %; every jump 82 % / 90 %;
    height of the >= 3 m jumps 0.65 m (14 %) from Surfr's."""
    r = SC.pool([_compare(n) for n in SURFR_DAYS]); sc = SC.scores(r)
    assert sc["big_found"] >= 0.88 and sc["big_confirmed"] >= 0.91, r
    assert sc["found"] >= 0.80 and sc["confirmed"] >= 0.87, r
    assert sc["h_mae"] <= 0.75 and abs(sc["h_bias"]) <= 0.35 and sc["h_within_1m"] >= 0.70, sc


def test_session_best_is_a_real_jump():
    """On the three days with exact Surfr times for the big jumps, KiteLog's three highest jumps are
    all in Surfr's list; the calm day (session 10) must not show a 6-8 m jump Surfr never saw."""
    for name in SURFR_DAYS[:3]:
        r = _compare(name); top = r["jumps"].sort_values("height_m").tail(3)
        assert set(top.ts_to) <= set(r["pairs"].ts), name
    assert _compare(SURFR_DAYS[3])["jumps"].height_m.max() < 6.0


@pytest.mark.parametrize("name,lo,hi,max_h", [
    ("2026-11-03_brazil-1.fit", 14, 22, 5.5),     # first session: "a jump on ~70 % of the turns, the largest 4, at most 5 m"
    ("2026-11-03_brazil-4.fit", 7, 12, 6.0),
    ("2026-11-05_brazil-6.fit", 6, 10, 6.0),
    ("2026-11-05_brazil-7.fit", 15, 24, 10.0),    # overpowered afternoon: ~9 m is plausible, the old 11.4 m riding-off "jump" is not
])
def test_sessions_without_reference(name, lo, hi, max_h):
    acc = _jumps(name)
    assert lo <= len(acc) <= hi, len(acc)
    assert acc.height_m.max() < max_h and acc.airtime_s.max() < 6.5
