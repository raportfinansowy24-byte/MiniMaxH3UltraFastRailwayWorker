# MiniMax H3 Ultra Fast Railway Worker

Railway worker for automated MiniMax H3 Ultra Fast video generation through Hugging Face.

## Architecture

Make.com
    ↓
Gemini
    ↓
Railway Worker
    ↓
Hugging Face MiniMax H3 Ultra Fast
    ↓
MP4 + embedded audio
    ↓
Make.com / FFmpeg
    ↓
Final 15-second YouTube Short

## Important

The current public Hugging Face H3 Space generates up to 14 seconds.

Therefore this worker intentionally generates a 14-second source video.

The downstream FFmpeg stage must create the final 15-second video.

## Features

- FastAPI API
- Hugging Face Gradio client
- MiniMax H3 Ultra Fast
- 9:16 generation
- Embedded H3 audio
- Polish narration instruction
- Single-worker queue
- Retry with exponential backoff
- HTTP webhook callback
- Job status endpoint
- Video download endpoint
- Railway compatible
- No GPU required on Railway worker

## Endpoints

### Health

GET:

/health

### Create render job

POST:

/render-sequence

Example:

{
  "prompt": "cinematic financial news video...",
  "webhookUrl": "https://hook.make.com/...",
  "jobId": "optional-job-id"
}

### Check status

GET:

/status/JOB_ID

### Download video

GET:

/download/JOB_ID

## Environment variables

HF_TOKEN
HF_SPACE_ID
H3_DURATION
H3_STEPS
MAX_RETRIES
RETRY_BASE_SECONDS
PUBLIC_BASE_URL
OUTPUT_DIR

## Recommended configuration

HF_SPACE_ID=mrfakename/minimax-h3-ultra-fast

H3_DURATION=14

H3_STEPS=8

MAX_RETRIES=4

RETRY_BASE_SECONDS=15

## Railway

Deploy this repository to Railway.

Set:

HF_TOKEN

HF_SPACE_ID

H3_DURATION

H3_STEPS

MAX_RETRIES

RETRY_BASE_SECONDS

PUBLIC_BASE_URL

Railway automatically provides PORT.

The Procfile starts:

uvicorn server:app --host 0.0.0.0 --port $PORT

## Make.com

Send:

POST /render-sequence

Content-Type:

application/json

Body:

{
  "prompt": "{{Gemini prompt}}",
  "webhookUrl": "{{Make webhook URL}}",
  "jobId": "{{unique job id}}"
}

The worker returns:

{
  "jobId": "...",
  "status": "queued",
  "durationGenerated": 14,
  "durationFinalTarget": 15,
  "audio": "embedded_by_h3"
}

After generation the worker sends the webhook:

{
  "jobId": "...",
  "status": "completed",
  "videoUrl": "...",
  "downloadUrl": "...",
  "durationGenerated": 14,
  "durationFinalTarget": 15,
  "audio": "embedded_by_h3",
  "format": "mp4"
}

## Audio

MiniMax H3 generates video and audio together.

The worker does not strip the audio.

## Quota

Do not rotate multiple Hugging Face tokens to bypass account-level quotas.

If Hugging Face returns a temporary 429/503/timeout, the worker retries with exponential backoff.

For quota efficiency H3_STEPS can be changed from 8 to 4.

## Production note

Railway filesystem is temporary.

For the first version the worker keeps generated files in:

/tmp/h3-output

For a production system, permanent object storage can be added later.
