"""KiteLog cloud service: upload a FIT (or Garmin "Export Original" zip), run the Python
pipeline, archive the file + results, and serve the browser report app.

    POST /api/analyze            multipart "file" -> JSON {session_id, stats, jumps[]}
    GET  /api/sessions           list archived sessions (newest first)
    GET  /api/sessions/{id}/{f}  download an archived file (fit, jumps.csv, session.json, ...)
    GET  /                       the web/ report app (static)

Storage: Google Cloud Storage bucket KITELOG_BUCKET, or ./data when unset (local dev).
Auth:    optional shared token KITELOG_TOKEN (header X-KiteLog-Token or ?token=).
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import io
import json
import os
import sys
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from kitelog.fitio import read_fit                       # noqa: E402
from kitelog.detect import build_streams, detect_jumps, DetectParams   # noqa: E402
from kitelog.height import estimate_height, HeightParams  # noqa: E402
from kitelog.session import jumps_table, session_stats   # noqa: E402

BUCKET = os.environ.get("KITELOG_BUCKET", "")
TOKEN = os.environ.get("KITELOG_TOKEN", "")
LOCAL_DIR = Path(os.environ.get("KITELOG_DATA_DIR", ROOT / "data"))
app = FastAPI(title="KiteLog", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


# ------------------------------------------------------------------ storage
class Store:
    def __init__(self):
        self.bucket = None
        if BUCKET:
            from google.cloud import storage
            self.bucket = storage.Client().bucket(BUCKET)
        else:
            LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, content_type="application/octet-stream"):
        if self.bucket:
            self.bucket.blob(key).upload_from_string(data, content_type=content_type)
        else:
            p = LOCAL_DIR / key
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        if self.bucket:
            b = self.bucket.blob(key)
            return b.download_as_bytes() if b.exists() else None
        p = LOCAL_DIR / key
        return p.read_bytes() if p.exists() else None

    def list_sessions(self) -> list[dict]:
        out = {}
        if self.bucket:
            for b in self.bucket.list_blobs(prefix="sessions/"):
                parts = b.name.split("/")
                if len(parts) >= 3:
                    out.setdefault(parts[1], []).append(parts[2])
        else:
            for d in (LOCAL_DIR / "sessions").glob("*") if (LOCAL_DIR / "sessions").exists() else []:
                out[d.name] = [f.name for f in d.iterdir()]
        return [dict(session_id=k, files=sorted(v)) for k, v in sorted(out.items(), reverse=True)]


store = Store()


def check_token(request: Request):
    if not TOKEN:
        return
    tok = request.headers.get("x-kitelog-token") or request.query_params.get("token")
    if tok != TOKEN:
        raise HTTPException(status_code=401, detail="bad token")


def unwrap(data: bytes, name: str) -> bytes:
    if data[8:12] == b".FIT":
        return data
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fit")] or z.namelist()
            if not names:
                raise HTTPException(400, "zip contains no .fit")
            return z.read(names[0])
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    raise HTTPException(400, f"unrecognised file {name}")


# ------------------------------------------------------------------ api
@app.post("/api/analyze")
async def analyze(request: Request, file: UploadFile = File(...)):
    check_token(request)
    raw = unwrap(await file.read(), file.filename or "upload")
    with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name
    try:
        rec, info = read_fit(tmp_path)
    finally:
        os.unlink(tmp_path)
    if len(rec) == 0:
        raise HTTPException(400, "no record messages in file")
    try:
        s = build_streams(rec)
    except Exception as e:                       # not a KiteLog recording
        raise HTTPException(400, f"no KiteLog developer data: {e}")
    cands, F = detect_jumps(s, DetectParams())
    heights = [estimate_height(c.t_to, c.t_land, s.press_t, s.press, HeightParams()) for c in cands]
    jumps = jumps_table(s, cands, heights)
    stats = session_stats(s, jumps)
    start = dt.datetime.fromtimestamp(float(rec["ts"].iloc[0]), dt.timezone.utc)
    sid = start.strftime("%Y-%m-%d_%H%M") + "_" + hashlib.sha1(raw).hexdigest()[:8]
    prefix = f"sessions/{sid}/"
    store.put(prefix + "activity.fit", raw)
    store.put(prefix + "jumps.csv", jumps.to_csv(index=False).encode(), "text/csv")
    result = dict(session_id=sid, start_utc=start.isoformat(), stats=asdict(stats),
                  jumps=json.loads(jumps.drop(columns=[c for c in jumps.columns if c.startswith("rough")]).to_json(orient="records")),
                  fit=dict(n_records=info["n_records"], sport=str(info["sport"]), errors=info["errors"]))
    store.put(prefix + "session.json", json.dumps(result, indent=1, default=float).encode(), "application/json")
    return JSONResponse(result)


@app.get("/api/sessions")
def sessions(request: Request):
    check_token(request)
    return store.list_sessions()


@app.get("/api/sessions/{sid}/{fname}")
def session_file(sid: str, fname: str, request: Request):
    check_token(request)
    if "/" in sid or "/" in fname or ".." in sid or ".." in fname:
        raise HTTPException(400, "bad path")
    data = store.get(f"sessions/{sid}/{fname}")
    if data is None:
        raise HTTPException(404, "not found")
    ct = {"csv": "text/csv", "json": "application/json", "fit": "application/octet-stream"}.get(fname.rsplit(".", 1)[-1], "application/octet-stream")
    return Response(content=data, media_type=ct)


@app.get("/healthz")
def healthz():
    return {"ok": True, "bucket": BUCKET or "local"}


app.mount("/", StaticFiles(directory=str(ROOT / "web"), html=True), name="web")
