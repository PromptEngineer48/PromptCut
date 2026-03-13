"""
FastAPI Server — Silence Remover API
Exposes the silence removal pipeline as a REST API.

Endpoints:
    POST /detect          — Detect silences only (returns timestamps)
    POST /process         — Full process: detect + export edited video
    GET  /jobs/{job_id}   — Poll job status (async processing)

Run with:
    uvicorn api.server:app --reload --port 8000
"""

import os
import uuid
import asyncio
import shutil
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Add parent to path so core imports work
import sys
sys.path.append(str(Path(__file__).parent.parent))

from core.detector import DetectionConfig
from core.pipeline import SilenceRemover, ProcessingResult

# ─── Config ────────────────────────────────────────────────────────────────────

UPLOAD_DIR = Path("/tmp/silence_remover/uploads")
OUTPUT_DIR = Path("/tmp/silence_remover/outputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Silence Remover API",
    description="Detect and remove silences from video files",
    version="1.0.0"
)

# In-memory job store (use Redis in production)
jobs: dict = {}


# ─── Schemas ───────────────────────────────────────────────────────────────────

class DetectResponse(BaseModel):
    duration: float
    silence_count: int
    time_removable: float
    percent_removable: float
    segments_to_keep: list
    silence_intervals: list


class JobStatus(BaseModel):
    job_id: str
    status: str          # pending | processing | done | error
    progress: float      # 0-100
    created_at: str
    output_url: Optional[str] = None
    error: Optional[str] = None
    stats: Optional[dict] = None


# ─── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "Silence Remover API", "version": "1.0.0", "status": "ok"}


@app.post("/detect", response_model=DetectResponse)
async def detect_silences(
    file: UploadFile = File(...),
    threshold_db: float = Form(-35.0),
    min_silence_duration: float = Form(0.4),
    padding: float = Form(0.05),
):
    """
    Upload a video/audio file and get back silence intervals + keep segments.
    No video is exported — this is for previewing what will be cut.
    """
    # Save uploaded file
    job_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{job_id}_{file.filename}"

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        config = DetectionConfig(
            silence_threshold_db=threshold_db,
            min_silence_duration=min_silence_duration,
            padding=padding
        )
        remover = SilenceRemover(config=config)
        result = remover.preview(str(input_path))

        return DetectResponse(
            duration=result.original_duration,
            silence_count=len(result.silence_intervals),
            time_removable=result.time_saved,
            percent_removable=result.percent_removed,
            segments_to_keep=[
                {"start": s.start, "end": s.end, "duration": s.duration}
                for s in result.keep_segments
            ],
            silence_intervals=[
                {"start": s.start, "end": s.end, "duration": s.duration, "db": s.db_level}
                for s in result.silence_intervals
            ]
        )
    finally:
        input_path.unlink(missing_ok=True)


@app.post("/process", response_model=JobStatus)
async def process_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    threshold_db: float = Form(-35.0),
    min_silence_duration: float = Form(0.4),
    padding: float = Form(0.05),
    stream_copy: bool = Form(False),
):
    """
    Upload a video, process in background, poll /jobs/{job_id} for status.
    Download the result from /jobs/{job_id}/download when done.
    """
    job_id = str(uuid.uuid4())
    input_path = UPLOAD_DIR / f"{job_id}_{file.filename}"
    output_path = OUTPUT_DIR / f"{job_id}_output.mp4"

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Register job
    jobs[job_id] = {
        "status": "pending",
        "progress": 0,
        "created_at": datetime.utcnow().isoformat(),
        "input_path": str(input_path),
        "output_path": str(output_path),
    }

    # Run in background
    background_tasks.add_task(
        _run_job,
        job_id=job_id,
        input_path=str(input_path),
        output_path=str(output_path),
        config=DetectionConfig(
            silence_threshold_db=threshold_db,
            min_silence_duration=min_silence_duration,
            padding=padding
        ),
        stream_copy=stream_copy
    )

    return JobStatus(
        job_id=job_id,
        status="pending",
        progress=0,
        created_at=jobs[job_id]["created_at"]
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    """Poll job status. When status='done', use /jobs/{job_id}/download."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    return JobStatus(
        job_id=job_id,
        status=job["status"],
        progress=job.get("progress", 0),
        created_at=job["created_at"],
        output_url=f"/jobs/{job_id}/download" if job["status"] == "done" else None,
        error=job.get("error"),
        stats=job.get("stats")
    )


@app.get("/jobs/{job_id}/download")
def download_result(job_id: str):
    """Download the processed video."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job not ready: {job['status']}")

    output_path = job["output_path"]
    if not Path(output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")

    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=f"processed_{job_id}.mp4"
    )


# ─── Background Worker ──────────────────────────────────────────────────────────

async def _run_job(
    job_id: str,
    input_path: str,
    output_path: str,
    config: DetectionConfig,
    stream_copy: bool
):
    """Run the silence removal pipeline in the background."""
    jobs[job_id]["status"] = "processing"

    def on_progress(p: float):
        jobs[job_id]["progress"] = p

    try:
        remover = SilenceRemover(config=config)

        # Run blocking code in thread pool
        loop = asyncio.get_event_loop()
        result: ProcessingResult = await loop.run_in_executor(
            None,
            lambda: remover.process(
                input_path=input_path,
                output_path=output_path,
                stream_copy=stream_copy,
                on_progress=on_progress
            )
        )

        jobs[job_id].update({
            "status": "done",
            "progress": 100,
            "stats": {
                "original_duration": result.original_duration,
                "output_duration": result.output_duration,
                "time_saved": result.time_saved,
                "percent_removed": result.percent_removed,
                "silences_found": len(result.silence_intervals),
                "segments_kept": len(result.keep_segments),
            }
        })

    except Exception as e:
        jobs[job_id].update({
            "status": "error",
            "error": str(e)
        })
        print(f"[Job {job_id}] Error: {e}")

    finally:
        Path(input_path).unlink(missing_ok=True)
