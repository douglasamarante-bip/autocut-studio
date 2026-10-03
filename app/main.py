from __future__ import annotations
import os, uuid, threading
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from .editor import process_video

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
OUTPUTS = BASE / "outputs"
UPLOADS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
app = FastAPI(title="AutoCut Studio")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
JOBS = {}


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/jobs")
async def create_job(
    video: UploadFile = File(...),
    music: UploadFile | None = File(None),
    ratio: str = Form("9:16"),
    smart_cut: bool = Form(True),
    dynamic_edit: bool = Form(True),
    edit_intensity: str = Form("balanced"),
    captions: bool = Form(True),
    caption_style: str = Form("creator"),
):
    if ratio not in {"9:16", "1:1", "16:9"}:
        raise HTTPException(400, "ratio inválido")
    if edit_intensity not in {"soft", "balanced", "aggressive"}:
        raise HTTPException(400, "intensidade inválida")
    if caption_style not in {"impact", "clean", "creator"}:
        raise HTTPException(400, "estilo de legenda inválido")

    jid = uuid.uuid4().hex[:12]
    src = UPLOADS / f"{jid}_{Path(video.filename or 'video.mp4').name}"
    with open(src, "wb") as f:
        while chunk := await video.read(1024 * 1024):
            f.write(chunk)

    music_path = None
    if music:
        music_path = UPLOADS / f"{jid}_music_{Path(music.filename or 'music.mp3').name}"
        with open(music_path, "wb") as f:
            while chunk := await music.read(1024 * 1024):
                f.write(chunk)

    out = OUTPUTS / f"{jid}.mp4"
    JOBS[jid] = {"status": "queued", "progress": 5, "output": None, "error": None}

    def worker():
        try:
            JOBS[jid].update(status="processing", progress=20)
            meta = process_video(
                str(src), str(out), ratio, smart_cut, captions, caption_style,
                str(music_path) if music_path else None,
                os.getenv("GROQ_API_KEY"), dynamic_edit, edit_intensity,
            )
            JOBS[jid].update(status="done", progress=100, output=f"/api/jobs/{jid}/download", meta=meta)
        except Exception as e:
            JOBS[jid].update(status="error", progress=100, error=str(e)[-1800:])
        finally:
            for p in [src, music_path]:
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass

    threading.Thread(target=worker, daemon=True).start()
    return {"id": jid, "status": "queued"}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    if jid not in JOBS:
        raise HTTPException(404, "job não encontrado")
    return JOBS[jid]


@app.get("/api/jobs/{jid}/download")
def download(jid: str):
    p = OUTPUTS / f"{jid}.mp4"
    if not p.exists():
        raise HTTPException(404, "arquivo não encontrado")
    return FileResponse(p, media_type="video/mp4", filename=f"autocut-{jid}.mp4")
