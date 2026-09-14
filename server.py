import os
import uuid
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from gradio_client import Client


# ============================================================
# CONFIG
# ============================================================

APP_NAME = "MiniMax H3 Ultra Fast Railway Worker"

HF_SPACE_ID = os.getenv(
    "HF_SPACE_ID",
    "mrfakename/minimax-h3-ultra-fast"
)

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "https://minimaxh3ultrafastrailwayworker-production.up.railway.app"
).rstrip("/")

OUTPUT_DIR = Path(
    os.getenv("OUTPUT_DIR", "/tmp/h3-output")
)

H3_DURATION = min(
    float(os.getenv("H3_DURATION", "14")),
    14.0
)

H3_STEPS = int(
    os.getenv("H3_STEPS", "8")
)

MAX_RETRIES = int(
    os.getenv("MAX_RETRIES", "4")
)

RETRY_BASE_SECONDS = int(
    os.getenv("RETRY_BASE_SECONDS", "15")
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(APP_NAME)


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version="1.0.0"
)


# ============================================================
# JOB STORAGE
# ============================================================

jobs = {}

jobs_lock = threading.Lock()

executor = ThreadPoolExecutor(
    max_workers=1
)


# ============================================================
# REQUEST MODEL
# ============================================================

class RenderRequest(BaseModel):
    prompt: str
    webhookUrl: str | None = None
    jobId: str | None = None


# ============================================================
# HELPERS
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat()


def build_prompt(user_prompt: str) -> str:
    return f"""
Create a cinematic vertical 9:16 short video.

IMPORTANT AUDIO REQUIREMENTS:
- Generate synchronized narration together with the video.
- Narration language: Polish.
- Natural Polish speaking voice.
- The narration must match the visual story.
- Audio must be embedded directly into the final MP4.
- Do not use English narration.
- Do not rely on subtitles as a replacement for narration.

VIDEO REQUIREMENTS:
- Vertical 9:16 composition.
- Strong cinematic visual quality.
- Dynamic but coherent camera movement.
- Clear subject.
- Strong visual hook in the first seconds.
- No watermark.
- No unnecessary text overlays.
- Keep the visual story coherent from beginning to end.

USER STORY / PROMPT:
{user_prompt}
""".strip()


def extract_video_path(result):
    """
    Gradio can return different structures depending on
    Space/client version. Search recursively for an MP4 path.
    """

    if result is None:
        return None

    if isinstance(result, str):
        if ".mp4" in result.lower():
            return result
        return None

    if isinstance(result, dict):
        for value in result.values():
            found = extract_video_path(value)
            if found:
                return found

    if isinstance(result, (list, tuple)):
        for item in result:
            found = extract_video_path(item)
            if found:
                return found

    return None


def make_public_url(filename: str) -> str:
    return f"{PUBLIC_BASE_URL}/download/{filename}"


def classify_error(exc: Exception) -> str:
    text = str(exc).lower()

    if "429" in text:
        return "rate_limit"

    if "quota" in text:
        return "quota"

    if "503" in text:
        return "service_unavailable"

    if "timeout" in text:
        return "timeout"

    if "queue" in text:
        return "queue"

    if "not found" in text:
        return "not_found"

    return "unknown"


# ============================================================
# HUGGING FACE GENERATION
# ============================================================

def generate_with_hf(prompt: str, output_file: Path):
    logger.info("Connecting to Hugging Face Space: %s", HF_SPACE_ID)

    client_kwargs = {}

    if HF_TOKEN:
        client_kwargs["hf_token"] = HF_TOKEN

    client = Client(
        HF_SPACE_ID,
        **client_kwargs
    )

    logger.info(
        "Starting H3 generation | duration=%s | steps=%s",
        H3_DURATION,
        H3_STEPS
    )

    result = client.predict(
        prompt=build_prompt(prompt),
        image_path=None,
        last_image_path=None,
        canvas="544x960 · 9:16 fast",
        duration=H3_DURATION,
        steps=H3_STEPS,
        seed=42,
        upsample=False,
        acceleration="Balanced",
        lora_preset="None",
        lora_repo="",
        lora_filename="",
        lora_strength=1.0,
        generation_preset="Turbo 8-step — faster, cleaner",
        references=None,
        api_name="/generate"
    )

    logger.info("Hugging Face generation finished")

    source_path = extract_video_path(result)

    if not source_path:
        raise RuntimeError(
            f"Hugging Face returned no MP4 path. Result: {result}"
        )

    logger.info("Generated source: %s", source_path)

    # Gradio may return a local temporary path.
    source = Path(source_path)

    if source.exists():
        source.replace(output_file)
        return output_file

    # Otherwise download the returned URL/path.
    if str(source_path).startswith(("http://", "https://")):
        response = requests.get(
            str(source_path),
            timeout=300
        )

        response.raise_for_status()

        output_file.write_bytes(
            response.content
        )

        return output_file

    # Some Gradio results may point to a /tmp file that isn't
    # visible through Path.exists() in the current process.
    try:
        response = requests.get(
            str(source_path),
            timeout=300
        )

        response.raise_for_status()

        output_file.write_bytes(
            response.content
        )

        return output_file

    except Exception as exc:
        raise RuntimeError(
            f"Unable to retrieve generated MP4: {source_path}"
        ) from exc


# ============================================================
# WEBHOOK
# ============================================================

def send_webhook(webhook_url: str, payload: dict):
    if not webhook_url:
        return

    try:
        logger.info(
            "Sending webhook: %s",
            webhook_url
        )

        response = requests.post(
            webhook_url,
            json=payload,
            timeout=30
        )

        response.raise_for_status()

        logger.info(
            "Webhook delivered: HTTP %s",
            response.status_code
        )

    except Exception as exc:
        logger.error(
            "Webhook failed: %s",
            exc
        )


# ============================================================
# BACKGROUND JOB
# ============================================================

def process_job(
    job_id: str,
    prompt: str,
    webhook_url: str | None
):

    filename = f"{job_id}.mp4"
    output_file = OUTPUT_DIR / filename

    with jobs_lock:
        jobs[job_id]["status"] = "processing"
        jobs[job_id]["startedAt"] = now()

    logger.info(
        "JOB %s started",
        job_id
    )

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            logger.info(
                "JOB %s attempt %s/%s",
                job_id,
                attempt,
                MAX_RETRIES
            )

            if output_file.exists():
                output_file.unlink()

            generate_with_hf(
                prompt,
                output_file
            )

            if not output_file.exists():
                raise RuntimeError(
                    "Generation completed but MP4 file does not exist"
                )

            file_size = output_file.stat().st_size

            if file_size < 1000:
                raise RuntimeError(
                    "Generated MP4 is suspiciously small"
                )

            video_url = make_public_url(filename)

            download_url = video_url

            payload = {
                "success": True,
                "status": "completed",
                "jobId": job_id,
                "videoUrl": video_url,
                "downloadUrl": download_url,
                "filename": filename,
                "generatedDuration": H3_DURATION,
                "finalTargetDuration": 15,
                "audioEmbedded": True,
                "format": "mp4",
                "message": (
                    "MiniMax H3 Ultra Fast generation completed. "
                    "Final 15-second duration should be handled "
                    "downstream by FFmpeg/combiner."
                )
            }

            with jobs_lock:
                jobs[job_id].update(
                    {
                        "status": "completed",
                        "completedAt": now(),
                        "videoUrl": video_url,
                        "downloadUrl": download_url,
                        "filename": filename,
                        "generatedDuration": H3_DURATION,
                        "fileSize": file_size,
                        "audioEmbedded": True
                    }
                )

            logger.info(
                "JOB %s completed successfully | %.2f MB",
                job_id,
                file_size / 1024 / 1024
            )

            send_webhook(
                webhook_url,
                payload
            )

            return

        except Exception as exc:

            last_error = exc

            error_type = classify_error(exc)

            logger.error(
                "JOB %s failed attempt %s | type=%s | error=%s",
                job_id,
                attempt,
                error_type,
                exc
            )

            if attempt < MAX_RETRIES:

                delay = RETRY_BASE_SECONDS * attempt

                logger.info(
                    "JOB %s retrying in %s seconds",
                    job_id,
                    delay
                )

                time.sleep(delay)

    # --------------------------------------------------------
    # FINAL FAILURE
    # --------------------------------------------------------

    error_message = str(last_error)

    payload = {
        "success": False,
        "status": "failed",
        "jobId": job_id,
        "error": error_message,
        "errorType": classify_error(last_error),
        "attempts": MAX_RETRIES
    }

    with jobs_lock:
        jobs[job_id].update(
            {
                "status": "failed",
                "completedAt": now(),
                "error": error_message,
                "errorType": classify_error(last_error),
                "attempts": MAX_RETRIES
            }
        )

    logger.error(
        "JOB %s permanently failed",
        job_id
    )

    send_webhook(
        webhook_url,
        payload
    )


# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def root():
    return {
        "service": APP_NAME,
        "status": "online",
        "version": "1.0.0",
        "space": HF_SPACE_ID
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": APP_NAME,
        "timestamp": now(),
        "hfSpace": HF_SPACE_ID,
        "h3Duration": H3_DURATION,
        "h3Steps": H3_STEPS
    }


@app.post("/render-sequence")
def render_sequence(request: RenderRequest):

    if not request.prompt:
        raise HTTPException(
            status_code=400,
            detail="prompt is required"
        )

    job_id = request.jobId or str(uuid.uuid4())

    with jobs_lock:

        if job_id in jobs:
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id} already exists"
            )

        jobs[job_id] = {
            "jobId": job_id,
            "status": "queued",
            "createdAt": now(),
            "prompt": request.prompt
        }

    logger.info(
        "JOB %s queued",
        job_id
    )

    executor.submit(
        process_job,
        job_id,
        request.prompt,
        request.webhookUrl
    )

    return {
        "success": True,
        "status": "queued",
        "jobId": job_id,
        "statusUrl": f"{PUBLIC_BASE_URL}/status/{job_id}",
        "message": "Render job queued successfully."
    }


@app.get("/status/{job_id}")
def get_status(job_id: str):

    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return job


@app.get("/download/{filename}")
def download_video(filename: str):

    # Prevent path traversal.
    safe_filename = Path(filename).name

    if safe_filename != filename:
        raise HTTPException(
            status_code=400,
            detail="Invalid filename"
        )

    file_path = OUTPUT_DIR / safe_filename

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Video file not found"
        )

    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=safe_filename
    )


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
def startup_event():
    logger.info("=" * 60)
    logger.info("%s", APP_NAME)
    logger.info("HF Space: %s", HF_SPACE_ID)
    logger.info("H3 duration: %s seconds", H3_DURATION)
    logger.info("H3 steps: %s", H3_STEPS)
    logger.info("Public URL: %s", PUBLIC_BASE_URL)
    logger.info("Output directory: %s", OUTPUT_DIR)
    logger.info("Render endpoint: POST /render-sequence")
    logger.info("=" * 60)


@app.on_event("shutdown")
def shutdown_event():
    logger.info("Shutting down worker...")
    executor.shutdown(wait=False)
