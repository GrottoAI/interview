"""Transcription proxy service.

Proxies WebSocket connections to AssemblyAI's streaming API while enforcing
connection health via application-level keepalive monitoring.  This is an
infrastructure component — candidates should treat it as a black box.
"""

import asyncio
import json
import logging
import os
import time
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

AAI_WS_HOST = "streaming.assemblyai.com"
AAI_WS_VERSION = "2025-05-12"
AAI_API_KEY = os.environ.get("ASSEMBLYAI_API_KEY", "")
SPEECH_MODEL = os.environ.get("SPEECH_MODEL", "universal-streaming-english")
SAMPLE_RATE = int(os.environ.get("SAMPLE_RATE", "16000"))
ENCODING = os.environ.get("ENCODING", "pcm_s16le")

# Connection health: clients must send data at least this often.
# Connections that go idle are closed to free upstream AAI resources.
KEEPALIVE_TIMEOUT = int(os.environ.get("KEEPALIVE_TIMEOUT", "4"))


async def handle_session(client_ws):
    """Proxy a single transcription session to AssemblyAI."""
    remote = client_ws.remote_address
    logger.info("New session from %s", remote)

    # Connect upstream to AAI
    params = {
        "speech_model": SPEECH_MODEL,
        "sample_rate": SAMPLE_RATE,
        "encoding": ENCODING,
    }
    uri = f"wss://{AAI_WS_HOST}/v3/ws?{urlencode(params)}"
    headers = {
        "Authorization": AAI_API_KEY,
        "AssemblyAI-Version": AAI_WS_VERSION,
    }

    try:
        aai_ws = await ws_connect(uri, additional_headers=headers, open_timeout=10)
    except Exception:
        logger.exception("Failed to connect to AAI for %s", remote)
        await client_ws.close(4002, "upstream connection failed")
        return

    last_activity = time.monotonic()
    closed = False

    async def check_keepalive():
        nonlocal closed
        while not closed:
            await asyncio.sleep(1)
            elapsed = time.monotonic() - last_activity
            if elapsed > KEEPALIVE_TIMEOUT:
                logger.warning(
                    "Keepalive timeout for %s (%.1fs since last activity)",
                    remote, elapsed,
                )
                closed = True
                await client_ws.close(4001, "keepalive timeout")
                await aai_ws.close()
                return

    async def client_to_aai():
        """Forward client messages (audio + control) to AAI."""
        nonlocal last_activity, closed
        try:
            async for message in client_ws:
                if closed:
                    break
                if isinstance(message, bytes):
                    await aai_ws.send(message)
                elif isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "keepalive":
                        last_activity = time.monotonic()
                        continue
                    elif data.get("type") == "Terminate":
                        await aai_ws.send(message)
                        break
                    else:
                        await aai_ws.send(message)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            if not closed:
                logger.exception("Error in client->AAI for %s", remote)
        finally:
            closed = True

    async def aai_to_client():
        """Forward AAI responses (transcripts) to client."""
        nonlocal closed
        try:
            async for message in aai_ws:
                if closed:
                    break
                await client_ws.send(message)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            if not closed:
                logger.exception("Error in AAI->client for %s", remote)
        finally:
            closed = True

    keepalive_task = asyncio.create_task(check_keepalive())

    try:
        await asyncio.gather(client_to_aai(), aai_to_client())
    finally:
        keepalive_task.cancel()
        try:
            await aai_ws.close()
        except Exception:
            pass
        logger.info("Session ended for %s", remote)


async def main():
    if not AAI_API_KEY:
        logger.error("ASSEMBLYAI_API_KEY not set!")
        return

    port = int(os.environ.get("PORT", "9001"))
    logger.info("Transcription proxy starting on :%d", port)
    logger.info("Upstream: %s, keepalive timeout: %ds", AAI_WS_HOST, KEEPALIVE_TIMEOUT)

    async with serve(
        handle_session,
        "0.0.0.0",
        port,
        ping_interval=None,
        ping_timeout=None,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
