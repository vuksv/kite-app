"""Accelerometer sample packing - MUST stay identical to watch/source/Pack.mc.

Why: the Instinct 2X (Connect IQ 3.4) gives us 25 Hz x/y/z accelerometer
batches once per second, but a FIT *record* message can carry at most 16
developer fields / 256 bytes, array fields do not work on device, and the
device log file is capped at 5 KB.  So we compand each axis sample to one
signed byte and pack two samples (6 bytes = 48 bits) into one FIT DOUBLE
(IEEE-754 doubles represent every integer below 2^53 exactly).

13 DOUBLE fields -> 26 sample slots per record -> 25 samples/s + 1 spare.

Companding (per axis, input in g):
  |a| <= 3 g   : code = round(a * 32)              resolution 31 mg
  3 g < |a|    : code = sign * (96 + round((|a|-3) * 8))   resolution 125 mg, clips at +-6.875 g
  code -128    : reserved = "no sample" (unused slot)
"""
from __future__ import annotations

import math
import numpy as np

CODE_INVALID = -128
SAMPLES_PER_FIELD = 2
AXES = 3
N_ACC_FIELDS = 13
SLOTS_PER_RECORD = N_ACC_FIELDS * SAMPLES_PER_FIELD  # 26
SAMPLE_RATE = 25
ACC_FIELD_NAMES = [f"acc_{i}" for i in range(N_ACC_FIELDS)]

_LIN_LIMIT_G = 3.0
_LIN_SCALE = 32.0   # codes per g below 3 g
_HI_SCALE = 8.0     # codes per g above 3 g
_LIN_MAX_CODE = 96


def encode_g(a: float) -> int:
    """g -> int8 code (vectorised version below)."""
    if not math.isfinite(a):
        return CODE_INVALID
    s = -1 if a < 0 else 1
    m = abs(a)
    if m <= _LIN_LIMIT_G:
        c = int(round(m * _LIN_SCALE))
    else:
        c = _LIN_MAX_CODE + int(round((m - _LIN_LIMIT_G) * _HI_SCALE))
    if c > 127:
        c = 127
    return s * c


def encode_g_array(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    s = np.where(a < 0, -1, 1)
    m = np.abs(a)
    c = np.where(m <= _LIN_LIMIT_G,
                 np.rint(m * _LIN_SCALE),
                 _LIN_MAX_CODE + np.rint((m - _LIN_LIMIT_G) * _HI_SCALE))
    c = np.minimum(c, 127)
    out = (s * c).astype(np.int16)
    out[~np.isfinite(a)] = CODE_INVALID
    return out


def decode_code(c: int) -> float:
    if c == CODE_INVALID:
        return float("nan")
    s = -1.0 if c < 0 else 1.0
    m = abs(c)
    if m <= _LIN_MAX_CODE:
        return s * m / _LIN_SCALE
    return s * (_LIN_LIMIT_G + (m - _LIN_MAX_CODE) / _HI_SCALE)


def decode_code_array(c: np.ndarray) -> np.ndarray:
    c = np.asarray(c, dtype=np.int32)
    s = np.where(c < 0, -1.0, 1.0)
    m = np.abs(c).astype(float)
    g = np.where(m <= _LIN_MAX_CODE, m / _LIN_SCALE, _LIN_LIMIT_G + (m - _LIN_MAX_CODE) / _HI_SCALE)
    g = s * g
    g[c == CODE_INVALID] = np.nan
    return g


def pack_pair(codes6) -> float:
    """6 int8 codes (s0x,s0y,s0z,s1x,s1y,s1z) -> float64 holding a 48-bit integer."""
    v = 0
    for c in codes6:
        v = (v << 8) | ((int(c) + 128) & 0xFF)
    return float(v)


def unpack_pair(d: float):
    v = int(round(d))
    out = []
    for i in range(6):
        b = (v >> (8 * (5 - i))) & 0xFF
        out.append(b - 128)
    return out


def pack_batch(codes_xyz: np.ndarray) -> list[float]:
    """codes_xyz: (n, 3) int codes, n <= 26 -> list of 13 doubles (unused slots = CODE_INVALID)."""
    n = len(codes_xyz)
    assert n <= SLOTS_PER_RECORD, n
    slots = np.full((SLOTS_PER_RECORD, AXES), CODE_INVALID, dtype=np.int32)
    slots[:n] = codes_xyz
    out = []
    for f in range(N_ACC_FIELDS):
        a, b = slots[2 * f], slots[2 * f + 1]
        out.append(pack_pair([a[0], a[1], a[2], b[0], b[1], b[2]]))
    return out


def unpack_batch(doubles) -> np.ndarray:
    """13 doubles -> (n, 3) codes with invalid slots removed (n <= 26)."""
    rows = []
    for d in doubles:
        if d is None or not np.isfinite(d):
            rows.append([CODE_INVALID] * 3)
            rows.append([CODE_INVALID] * 3)
            continue
        c = unpack_pair(d)
        rows.append(c[0:3])
        rows.append(c[3:6])
    rows = np.array(rows, dtype=np.int32)
    valid = ~(rows == CODE_INVALID).all(axis=1)
    return rows[valid]


# --- meta field (UINT32) -----------------------------------------------------
# bits 0-15  : batch sequence number (wraps at 65536)
# bits 16-20 : number of valid samples in this batch (0..31)
# bits 21-23 : app state (0 idle, 1 recording, 2 paused)
# bits 24-30 : flags (bit24 = baro valid, bit25 = gps fix usable, bit26 = live-jump flagged)
def pack_meta(seq: int, n_valid: int, state: int, flags: int = 0) -> int:
    return (seq & 0xFFFF) | ((n_valid & 0x1F) << 16) | ((state & 0x7) << 21) | ((flags & 0x7F) << 24)


def unpack_meta(m: int) -> dict:
    m = int(m)
    return {"seq": m & 0xFFFF, "n_valid": (m >> 16) & 0x1F, "state": (m >> 21) & 0x7, "flags": (m >> 24) & 0x7F}
