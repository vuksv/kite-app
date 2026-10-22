// Pack.mc - sample companding + bit packing.  MUST mirror tools/kitelog/packing.py.
//
// Each axis sample (milli-g) -> one signed byte:
//   |a| <= 3000 mg : code = round(a / 31.25)            (31 mg resolution)
//   |a| >  3000 mg : code = sign * (96 + round((|a|-3000) / 125))   clips at +-127 (= +-6.875 g)
//   -128 reserved  : "empty slot"
// Two samples (6 bytes) are packed into one 48-bit integer stored in a FIT
// DOUBLE field (doubles hold integers < 2^53 exactly).
using Toybox.Lang;

module Pack {
    const CODE_INVALID = -128;

    // milli-g (Number) -> code (Number in -127..127)
    function encodeMg(mg) {
        var neg = mg < 0;
        var m = neg ? -mg : mg;
        var c;
        if (m <= 3000) {
            c = (m * 32 + 500) / 1000;            // round(m / 31.25)
        } else {
            c = 96 + ((m - 3000) * 8 + 500) / 1000;
            if (c > 127) { c = 127; }
        }
        return neg ? -c : c;
    }

    // six codes -> Double (48-bit integer).  Order: s0x s0y s0z s1x s1y s1z (s0x most significant)
    function packPair(c0, c1, c2, c3, c4, c5) {
        var v = 0l;
        v = (v << 8) | (c0 + 128).toLong();
        v = (v << 8) | (c1 + 128).toLong();
        v = (v << 8) | (c2 + 128).toLong();
        v = (v << 8) | (c3 + 128).toLong();
        v = (v << 8) | (c4 + 128).toLong();
        v = (v << 8) | (c5 + 128).toLong();
        return v.toDouble();
    }

    // meta word (UINT32): seq | nValid<<16 | state<<21 | flags<<24   (bit 31 kept clear)
    function packMeta(seq, nValid, state, flags) {
        return (seq & 0xFFFF) | ((nValid & 0x1F) << 16) | ((state & 0x7) << 21) | ((flags & 0x7F) << 24);
    }
}
