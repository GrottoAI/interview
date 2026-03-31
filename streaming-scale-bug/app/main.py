"""StreamCo Live Transcription — FastAPI application."""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from redis_client import get_all_sessions, get_redis, get_transcripts, store_transcript
from transcription import TranscriptionClient, TRANSCRIBER_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

APP_START_TIME = time.time()
_active_sessions: dict[str, TranscriptionClient] = {}
_sessions_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StreamCo Transcription service starting...")
    yield
    with _sessions_lock:
        for sid, client in _active_sessions.items():
            client.disconnect()
        _active_sessions.clear()
    logger.info("StreamCo Transcription service stopped.")


app = FastAPI(title="StreamCo Live Transcription", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.websocket("/ws/transcribe")
async def transcribe_ws(ws: WebSocket):
    await ws.accept()

    session_id = f"session_{int(time.time())}_{id(ws) % 10000}"
    logger.info("New transcription session: %s", session_id)

    loop = asyncio.get_running_loop()
    transcript_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    def on_transcript(data: dict[str, Any]) -> None:
        """Called from the TranscriptionClient reader thread."""
        store_transcript(session_id, data)
        loop.call_soon_threadsafe(transcript_queue.put_nowait, data)

    client = TranscriptionClient(url=TRANSCRIBER_URL, on_transcript=on_transcript)

    try:
        await loop.run_in_executor(None, client.connect)
        with _sessions_lock:
            _active_sessions[session_id] = client

        async def relay_transcripts():
            while True:
                data = await transcript_queue.get()
                try:
                    await ws.send_json({
                        "type": "transcript",
                        "session_id": session_id,
                        "data": data,
                    })
                except Exception:
                    break

        relay_task = asyncio.create_task(relay_transcripts())

        while True:
            message = await ws.receive()

            if message["type"] == "websocket.receive":
                if "bytes" in message and message["bytes"]:
                    client.send_audio(message["bytes"])
                elif "text" in message and message["text"]:
                    try:
                        control = json.loads(message["text"])
                        if control.get("type") == "stop":
                            break
                    except json.JSONDecodeError:
                        pass
            elif message["type"] == "websocket.disconnect":
                break

    except WebSocketDisconnect:
        logger.info("Browser disconnected: %s", session_id)
    except Exception:
        logger.exception("Error in session %s", session_id)
    finally:
        relay_task.cancel()
        client.disconnect()
        with _sessions_lock:
            _active_sessions.pop(session_id, None)
        logger.info("Session %s cleaned up", session_id)


@app.get("/debug/info")
async def debug_info():
    start = time.monotonic()
    await asyncio.sleep(0)
    loop_lag_ms = (time.monotonic() - start) * 1000

    dispatch_start = time.monotonic()
    await asyncio.get_running_loop().run_in_executor(None, lambda: None)
    dispatch_ms = (time.monotonic() - dispatch_start) * 1000

    session_stats = {}
    with _sessions_lock:
        for sid, client in _active_sessions.items():
            session_stats[sid] = client.get_stats()

    return {
        "event_loop_lag_ms": round(loop_lag_ms, 2),
        "thread_dispatch_ms": round(dispatch_ms, 2),
        "active_threads": threading.active_count(),
        "thread_names": [t.name for t in threading.enumerate()],
        "gil_switch_interval_ms": round(sys.getswitchinterval() * 1000, 1),
        "active_sessions": len(_active_sessions),
        "session_details": session_stats,
        "uptime_s": round(time.time() - APP_START_TIME, 1),
        "cpu_count": os.cpu_count(),
    }


@app.get("/api/transcripts")
async def list_sessions():
    return {"sessions": get_all_sessions()}


@app.get("/api/transcripts/{session_id}")
async def get_session_transcripts(session_id: str):
    return {"session_id": session_id, "transcripts": get_transcripts(session_id)}


@app.get("/health")
async def health():
    return {"status": "ok", "uptime_s": round(time.time() - APP_START_TIME, 1)}
