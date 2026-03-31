# StreamCo Live Transcription — Debugging Exercise

## Background

StreamCo is a live transcription service. Users open the web UI, click
"Add Stream" to start capturing microphone audio, and see real-time
transcription results appear on screen. The app supports multiple
concurrent transcription streams.

## The Problem

The service works well with 1-2 concurrent streams. When running 4-5+
concurrent streams in our production environment (resource-constrained
containers), transcription connections start dropping — the transcription
service closes connections due to keepalive timeouts.

The issue does **not** occur when running on a developer MacBook with
full resources. It only manifests in the containerized environment.

**Your task: diagnose the root cause and implement a fix.**

## Setup

### Prerequisites

- Docker and Docker Compose
- [uv](https://docs.astral.sh/uv/) (for local development)
- An AssemblyAI API key (provided to you)
- ffmpeg (for the load test script)

### Running in Docker (reproduces the bug)

```bash
# Create .env with your API key
cp .env.example .env
# Edit .env and add your API key

# Start everything
docker compose up --build
```

Open http://localhost:8000 in your browser.

### Running locally (works fine — for comparison)

Run Redis in Docker, then run the app locally with full CPU:

```bash
# Start just Redis and the transcriber
docker compose up -d redis transcriber

# Install dependencies and run the app
cd app
uv sync
REDIS_URL=redis://localhost:16379 TRANSCRIBER_URL=ws://localhost:9001 uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Running locally with the same number of streams should work without issues.

### Load testing with the script

Instead of manually adding streams in the browser, you can use the
included load test script to stream an audio file:

```bash
# Install test dependencies
uv pip install websockets httpx

# Stream 1 session (should work fine)
uv run --with websockets --with httpx test_stream.py -n 1

# Stream 5 sessions (triggers the bug in Docker)
uv run --with websockets --with httpx test_stream.py -n 5
```

The script decodes `audio_bria.mp3` to PCM and streams it over WebSocket,
printing transcription results and debug info in real time.

## Reproducing the issue

1. Click **+ Add Stream** — transcription should work (text appears as you speak)
2. Add 4-5 more streams
3. Within 30-60 seconds, streams will start disconnecting (status changes
   from "live" to "closed")
4. The performance sidebar shows system metrics that may be useful

### Diagnostic endpoint

```
GET http://localhost:8000/debug/info
```

Returns event loop lag, thread counts, and session details.

## Architecture

```
Browser (mic) ──WebSocket──▶ FastAPI app ──WebSocket──▶ Transcription Service
              ◀─transcripts─            ◀─transcripts─

                                │
                                ▼
                              Redis (transcript storage)
```

### File structure

| File | Description |
|------|-------------|
| `app/main.py` | FastAPI application — WebSocket endpoint, debug info |
| `app/transcription.py` | TranscriptionClient — manages connections to the transcription service |
| `app/redis_client.py` | Redis client for transcript storage |
| `app/templates/index.html` | Frontend UI |
| `app/static/audio-processor.js` | Browser audio capture (AudioWorklet) |
| `app/pyproject.toml` | Python dependencies |
| `docker-compose.yml` | Container orchestration |
| `transcriber/` | Transcription service (treat as a black box — see below) |
| `test_stream.py` | Load test script |
| `audio_bria.mp3` | Sample audio for load testing |

### About the transcription service

The `transcriber/` directory contains our transcription infrastructure
service. It proxies audio to our speech-to-text provider and enforces
connection health monitoring. **Treat this as a third-party service you
cannot modify** — like an external API. The service:

- Accepts WebSocket connections
- Forwards audio to the upstream STT provider
- Sends back real-time transcription results
- Monitors connection health via application-level keepalive
- Closes connections that stop sending keepalive signals within the timeout window

The app's `TranscriptionClient` is responsible for maintaining the
connection and sending keepalive messages.

## Incident Log

See [incident-log.md](incident-log.md) for notes from the team's
initial investigation.
