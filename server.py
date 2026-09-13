import os
import time
import uuid
import shutil
import threading
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from gradio_client import Client


# ============================================================
# CONFIG
# ============================================================

APP_NAME = "MiniMax H3 Ultra Fast Railway Worker"

SPACE_ID = os.getenv(
    "HF_SPACE_ID",
    "mrfakename/minimax-h3-ultra-fast"
)

HF_TOKEN = os.getenv("HF_TOKEN")

# Public H3 Space currently supports up to 14 seconds.
H3_DURATION = min(
    float(os.getenv("H3_DURATION", "14")),
    14.0
)

# 8 = good speed/quality compromise.
# 4 = cheaper/faster ZeroGPU quota usage.
H3_STEPS = int(
    os.getenv("H3_STEPS", "8")
)

MAX_RETRIES = int(
    os.getenv("MAX_RETRIES", "4")
)

RETRY_BASE_SECONDS = float(
    os.getenv("RETRY_BASE_SECONDS", "15")
)

WEBHOOK_TIMEOUT = int(
    os.getenv("WEBHOOK_TIMEOUT", "30")
)

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    ""
).rstrip("/")

OUTPUT_DIR = Path(
    os.getenv(
        "OUTPUT_DIR",
        "/tmp/h3-output"
    )
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title=APP_NAME,
    version="1.0.0"
)


# ============================================================
# JOB STORAGE
# ============================================================

_jobs: dict[str, dict[str, Any]] = {}

_jobs_lock = threading.Lock()

# IMPORTANT:
# H3 ZeroGPU generation is serialized.
# We do not run multiple H3 generations simultaneously.
_executor = ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="h3-worker"
)


# ============================================================
# REQUEST MODEL
# ============================================================

class RenderRequest(BaseModel):

    prompt: str = Field(
        min_length=1,
        max_length=12000
    )

    webhookUrl: str | None = None

    jobId: str | None = None


# ============================================================
# HELPERS
# ============================================================

def now() -> float:
    return time.time()


def build_prompt(prompt: str) -> str:
    """
    Adds explicit audio instructions to the Gemini/H3 prompt.
    """

    audio_instruction = """

AUDIO REQUIREMENTS:
Generate synchronized spoken narration in Polish (pl-PL).
Use natural, clear Polish pronunciation.
The narration must be audible and embedded directly into the MP4.
Do not use English narration.
Do not create subtitles as a replacement for narration.
Keep the narration synchronized with the generated video.
"""

    return prompt.rstrip() + audio_instruction


def extract_video_path(result: Any) -> str:
    """
    Extract an MP4/video path from different possible
    Gradio result structures.
    """

    if isinstance(result, str):

        if result.lower().endswith(
            (".mp4", ".webm", ".mov", ".mkv")
        ):
            return result

    if isinstance(result, Path):

        return str(result)

    if isinstance(result, (list, tuple)):

        for item in result:

            try:
                return extract_video_path(item)

            except ValueError:
                continue

    if isinstance(result, dict):

        for key in (
            "video",
            "path",
            "value",
            "file"
        ):

            if key in result:

                try:
                    return extract_video_path(
                        result[key]
                    )

                except ValueError:
                    continue

    if hasattr(result, "path"):

        path = getattr(
            result,
            "path",
            None
        )

        if path:
            return str(path)

    raise ValueError(
        f"Could not extract video path from "
        f"Gradio result: {result!r}"
    )


def make_public_url(path: str) -> str:
    """
    Converts /download/<job_id> into an absolute URL.
    """

    if not PUBLIC_BASE_URL:

        return path

    return (
        f"{PUBLIC_BASE_URL}"
        f"{path}"
    )


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

    return "unknown"
