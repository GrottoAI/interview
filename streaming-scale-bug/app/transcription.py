"""TranscriptionClient — wraps the transcription service WebSocket API."""

import json
import logging
import os
import queue
import threading
import time
from typing import Any, Callable

from audio_utils import validate_audio_chunk

from websockets.sync.client import connect as ws_connect

logger = logging.getLogger(__name__)

TRANSCRIBER_URL = os.environ.get("TRANSCRIBER_URL", "ws://transcriber:9001")


class TranscriptionClient:
    """Real-time transcription session.

    Uses a reader/writer/keepalive thread model to manage the connection.
    """

    def __init__(
        self,
        url: str,
        on_transcript: Callable[[dict[str, Any]], None],
    ):
        self.url = url
        self.on_transcript = on_transcript

        self._ws = None
        self._write_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()
        self._ws_lock = threading.Lock()
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop, daemon=True)

        self._bytes_sent = 0
        self._transcripts_received = 0
        self._start_time: float | None = None

    def connect(self) -> None:
        logger.info("Connecting to transcription service at %s", self.url)
        self._ws = ws_connect(
            self.url,
            open_timeout=10,
            ping_interval=None,
            ping_timeout=None,
        )
        self._start_time = time.monotonic()
        self._reader_thread.start()
        self._writer_thread.start()
        self._keepalive_thread.start()
        logger.info("TranscriptionClient connected")

    def send_audio(self, chunk: bytes) -> None:
        if not self._stop_event.is_set():
            self._write_queue.put(chunk)

    def disconnect(self) -> None:
        self._stop_event.set()
        self._write_queue.put(None)
        if self._ws:
            try:
                with self._ws_lock:
                    self._ws.send(json.dumps({"type": "Terminate"}))
                self._ws.close()
            except Exception:
                pass
        logger.info("TranscriptionClient disconnected")

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set()

    def get_stats(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._start_time if self._start_time else 0
        return {
            "elapsed_s": round(elapsed, 1),
            "bytes_sent": self._bytes_sent,
            "transcripts_received": self._transcripts_received,
        }

    def _write_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                chunk = self._write_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None:
                break

            with self._ws_lock:
                try:
                    self._validate_and_send(chunk)
                except Exception:
                    if not self._stop_event.is_set():
                        logger.warning("Write failed")
                    break

    def _validate_and_send(self, chunk: bytes) -> None:
        """Validate audio chunk and send it if it passes quality checks."""
        if not validate_audio_chunk(chunk):
            logger.warning("Dropping invalid audio chunk (%d bytes)", len(chunk))
            return

        self._ws.send(chunk)
        self._bytes_sent += len(chunk)

    def _read_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._ws_lock:
                    raw = self._ws.recv(timeout=0.01)
            except TimeoutError:
                continue
            except Exception as e:
                if not self._stop_event.is_set():
                    logger.warning("Connection lost: %s", e)
                break

            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            msg_type = data.get("message_type") or data.get("type", "")

            if msg_type in ("PartialTranscript", "FinalTranscript", "Turn"):
                self._transcripts_received += 1
                if "end_of_turn" not in data:
                    data["end_of_turn"] = msg_type == "FinalTranscript"
                if "turn_order" not in data:
                    data["turn_order"] = data.get("audio_start", 0)
                try:
                    self.on_transcript(data)
                except Exception:
                    logger.exception("Callback error")

            elif msg_type in ("SessionBegins", "Begin"):
                logger.info("Session started: %s", data.get("session_id", data.get("id", "?")))

            elif "error" in data:
                logger.error("Transcription error: %s", data["error"])
                break

    def _keepalive_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=3)
            if self._stop_event.is_set():
                break
            try:
                with self._ws_lock:
                    self._ws.send(json.dumps({"type": "keepalive"}))
            except Exception:
                if not self._stop_event.is_set():
                    logger.warning("Keepalive send failed")
                break
