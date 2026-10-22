"""FIT file I/O.

read_fit(path)   -> records DataFrame with the same columns the simulator makes
                    (uses the official garmin-fit-sdk; developer fields are looked
                    up by the names the watch app gives them)
write_fit(records, path) -> synthetic FIT with the exact developer-field layout the
                    watch writes, so the decode path can be tested without a watch
                    (uses fit_tool).

Developer field layout (RECORD message, 16 fields, 128 bytes):
  id 0..12  acc_0..acc_12  DOUBLE   packed accelerometer samples (packing.py)
  id 13     press          FLOAT    raw ambient pressure, Pa (Activity.Info.rawAmbientPressure)
  id 14     tmr            UINT32   System.getTimer() ms at the accelerometer batch callback
  id 15     meta           UINT32   seq | n_valid | state | flags  (packing.pack_meta)
"""
from __future__ import annotations

import datetime as _dt
import math

import numpy as np
import pandas as pd

from . import packing as P

DEV_FIELDS = [(i, n, "DOUBLE", "") for i, n in enumerate(P.ACC_FIELD_NAMES)] + [
    (13, "press", "FLOAT", "Pa"),
    (14, "tmr", "UINT32", "ms"),
    (15, "meta", "UINT32", ""),
]
FIT_EPOCH = _dt.datetime(1989, 12, 31, tzinfo=_dt.timezone.utc)
SEMI = 180.0 / 2 ** 31
RECORD_COLUMNS = ["ts", "lat", "lon", "speed", "alt"] + P.ACC_FIELD_NAMES + ["press", "tmr", "meta"]


def _to_unix(ts):
    if isinstance(ts, _dt.datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        return ts.timestamp()
    return float(ts)


def read_fit(path: str) -> tuple[pd.DataFrame, dict]:
    from garmin_fit_sdk import Decoder, Stream
    stream = Stream.from_file(path)
    decoder = Decoder(stream)
    messages, errors = decoder.read(convert_datetimes_to_dates=True, expand_components=True)
    recs = messages.get("record_mesgs", [])
    rows = []
    for m in recs:
        dev = m.get("developer_fields", {}) or {}
        row = dict(ts=_to_unix(m.get("timestamp")),
                   lat=(m.get("position_lat") or np.nan) * SEMI if m.get("position_lat") is not None else np.nan,
                   lon=(m.get("position_long") or np.nan) * SEMI if m.get("position_long") is not None else np.nan,
                   speed=m.get("enhanced_speed", m.get("speed", np.nan)),
                   alt=m.get("enhanced_altitude", m.get("altitude", np.nan)))
        for fid, name, _, _ in DEV_FIELDS:
            v = dev.get(fid, dev.get(name, np.nan))
            if v is None:
                v = np.nan
            row[name] = v
        rows.append(row)
    df = pd.DataFrame(rows, columns=RECORD_COLUMNS)
    for c in ["tmr", "meta"]:
        df[c] = df[c].fillna(0).astype(np.int64)
    laps = messages.get("lap_mesgs", [])
    sess = messages.get("session_mesgs", [])
    info = dict(n_records=len(df), n_laps=len(laps), sport=(sess[0].get("sport") if sess else None),
                errors=[str(e) for e in errors], field_descriptions=messages.get("field_description_mesgs", []))
    return df, info


def write_fit(records: pd.DataFrame, path: str):
    """Synthetic FIT with the watch's developer-field layout (fit_tool encoder)."""
    from fit_tool.fit_file_builder import FitFileBuilder
    from fit_tool.profile.messages.file_id_message import FileIdMessage
    from fit_tool.profile.messages.developer_data_id_message import DeveloperDataIdMessage
    from fit_tool.profile.messages.field_description_message import FieldDescriptionMessage
    from fit_tool.profile.messages.record_message import RecordMessage
    from fit_tool.profile.messages.session_message import SessionMessage
    from fit_tool.profile.messages.event_message import EventMessage
    from fit_tool.profile.profile_type import FileType, Manufacturer, Sport, Event, EventType
    from fit_tool.developer_field import DeveloperField
    from fit_tool.base_type import BaseType

    base_map = {"DOUBLE": (BaseType.FLOAT64, 8), "FLOAT": (BaseType.FLOAT32, 4), "UINT32": (BaseType.UINT32, 4)}
    builder = FitFileBuilder(auto_define=True, min_string_size=50)
    t0_ms = int(records["ts"].iloc[0] * 1000)
    fid = FileIdMessage()
    fid.type = FileType.ACTIVITY
    fid.manufacturer = Manufacturer.DEVELOPMENT.value
    fid.product = 0
    fid.time_created = t0_ms
    fid.serial_number = 0x12345678
    builder.add(fid)

    ddi = DeveloperDataIdMessage()
    ddi.application_id = [0x11] * 16
    ddi.developer_data_index = 0
    builder.add(ddi)
    for fnum, name, typ, units in DEV_FIELDS:
        fdm = FieldDescriptionMessage()
        fdm.developer_data_index = 0
        fdm.field_definition_number = fnum
        fdm.fit_base_type_id = base_map[typ][0].value
        fdm.field_name = name
        fdm.units = units
        fdm.native_mesg_num = RecordMessage.ID
        builder.add(fdm)

    ev = EventMessage()
    ev.timestamp = t0_ms
    ev.event = Event.TIMER
    ev.event_type = EventType.START
    builder.add(ev)

    def make_dev_fields():
        out = []
        for fnum, name, typ, units in DEV_FIELDS:
            bt, size = base_map[typ]
            out.append(DeveloperField(field_id=fnum, name=name, base_type=bt, units=units, size=size,
                                      developer_data_index=0))
        return out

    for _, r in records.iterrows():
        devs = make_dev_fields()
        rm = RecordMessage(developer_fields=devs)
        rm.timestamp = int(r["ts"] * 1000)
        if np.isfinite(r["lat"]) and np.isfinite(r["lon"]):
            rm.position_lat = float(r["lat"])
            rm.position_long = float(r["lon"])
        if np.isfinite(r["speed"]):
            rm.enhanced_speed = float(r["speed"])
        for f, (fnum, name, typ, units) in zip(devs, DEV_FIELDS):
            v = r[name]
            f.set_value(0, float(v) if typ in ("DOUBLE", "FLOAT") else int(v))
        builder.add(rm)

    sm = SessionMessage()
    sm.timestamp = int(records["ts"].iloc[-1] * 1000)
    sm.start_time = t0_ms
    sm.sport = Sport.KITESURFING
    sm.total_elapsed_time = float(records["ts"].iloc[-1] - records["ts"].iloc[0])
    builder.add(sm)
    fit_file = builder.build()
    fit_file.to_file(path)
    return path
