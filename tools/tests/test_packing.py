"""Pack.mc <-> packing.py equivalence (run: python -m pytest tools/tests)."""
import numpy as np
import kitelog.packing as P


def encode_mg_monkeyc(mg: int) -> int:
    """Literal transcription of Pack.encodeMg (integer arithmetic, Monkey C semantics)."""
    neg = mg < 0
    m = -mg if neg else mg
    if m <= 3000:
        c = (m * 32 + 500) // 1000
    else:
        c = 96 + ((m - 3000) * 8 + 500) // 1000
        if c > 127:
            c = 127
    return -c if neg else c


def pack_pair_monkeyc(codes):
    v = 0
    for c in codes:
        v = (v << 8) | (c + 128)
    return float(v)


def test_encode_matches_monkeyc_for_every_milli_g():
    mg = np.arange(-9000, 9001)
    py = P.encode_g_array(mg / 1000.0)
    mc = np.array([encode_mg_monkeyc(int(v)) for v in mg])
    assert (py == mc).all()


def test_decode_resolution():
    for c in range(-127, 128):
        g = P.decode_code(c)
        assert P.encode_g(g) == c


def test_pack_roundtrip():
    rng = np.random.default_rng(1)
    codes = rng.integers(-127, 128, size=(25, 3))
    d = P.pack_batch(codes)
    assert len(d) == 13
    assert all(x < 2 ** 53 for x in d)
    back = P.unpack_batch(d)
    assert back.shape == (25, 3) and (back == codes).all()
    assert d[0] == pack_pair_monkeyc([*codes[0], *codes[1]])


def test_meta():
    m = P.pack_meta(65535, 25, 1, 0x7F)
    assert m < 2 ** 31
    assert P.unpack_meta(m) == {"seq": 65535, "n_valid": 25, "state": 1, "flags": 0x7F}
