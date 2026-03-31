"""Test script: stream an MP3 file to the transcription WebSocket.

Decodes the MP3 to raw PCM (16kHz, mono, int16) and sends it in ~100ms
chunks over WebSocket — simulating what the browser's AudioWorklet does.
Also polls /debug/info and prints event loop lag over time.
"""

import asyncio
import json
import subprocess
import sys
import time

import websockets
import httpx


AUDIO_FILE = "audio_bria.mp3"
WS_URL = "ws://localhost:8000/ws/transcribe"
DEBUG_URL = "http://localhost:8000/debug/info"
SAMPLE_RATE = 16000
CHUNK_DURATION_MS = 100  # send 100ms chunks, matching browser behavior
BYTES_PER_SAMPLE = 2  # int16
CHUNK_SIZE = SAMPLE_RATE * CHUNK_DURATION_MS // 1000 * BYTES_PER_SAMPLE  # 3200 bytes


def decode_mp3_to_pcm(path: str) -> bytes:
    """Decode MP3 to raw PCM s16le @ 16kHz mono using ffmpeg."""
    result = subprocess.run(
        [
            "ffmpeg", "-i", path,
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-loglevel", "error",
            "pipe:1",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"ffmpeg error: {result.stderr.decode()}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


async def stream_audio(pcm_data: bytes):
    """Stream PCM audio to the WebSocket and print transcripts + debug info."""
    print(f"Decoded {len(pcm_data)} bytes of PCM audio ({len(pcm_data) / SAMPLE_RATE / 2:.1f}s)")
    print(f"Will send in {len(pcm_data) // CHUNK_SIZE} chunks of {CHUNK_SIZE} bytes")
    print(f"Connecting to {WS_URL}...")

    async with websockets.connect(WS_URL) as ws:
        print("Connected!\n")

        transcript_count = 0
        start_time = time.monotonic()

        async def receive_transcripts():
            nonlocal transcript_count
            async for msg in ws:
                data = json.loads(msg)
                if data.get("type") == "transcript":
                    transcript_count += 1
                    td = data["data"]
                    is_final = td.get("end_of_turn", False)
                    text = td.get("transcript", "")
                    marker = "FINAL" if is_final else "partial"
                    elapsed = time.monotonic() - start_time
                    print(f"  [{elapsed:6.1f}s] [{marker:>7}] {text}")

        async def poll_debug():
            async with httpx.AsyncClient() as client:
                while True:
                    await asyncio.sleep(5)
                    try:
                        resp = await client.get(DEBUG_URL)
                        info = resp.json()
                        elapsed = time.monotonic() - start_time
                        print(
                            f"\n  === DEBUG [{elapsed:.0f}s] "
                            f"loop_lag={info['event_loop_lag_ms']:.1f}ms "
                            f"threads={info['active_threads']} "
                            f"dispatch={info.get('thread_dispatch_ms', 0):.1f}ms "
                            f"transcripts={info.get('session_details', {})}"
                            f" ===\n"
                        )
                    except Exception as e:
                        print(f"  [debug poll error: {e}]")

        recv_task = asyncio.create_task(receive_transcripts())
        debug_task = asyncio.create_task(poll_debug())

        # Send audio chunks at real-time pace
        offset = 0
        chunks_sent = 0
        while offset < len(pcm_data):
            chunk = pcm_data[offset : offset + CHUNK_SIZE]
            await ws.send(chunk)
            offset += CHUNK_SIZE
            chunks_sent += 1
            # Pace at real-time (100ms per chunk)
            await asyncio.sleep(CHUNK_DURATION_MS / 1000)

        print(f"\nDone sending {chunks_sent} chunks. Waiting for remaining transcripts...")

        # Wait a bit for final transcripts to arrive
        await asyncio.sleep(5)

        # Send stop
        await ws.send(json.dumps({"type": "stop"}))
        await asyncio.sleep(1)

        debug_task.cancel()
        recv_task.cancel()

        elapsed = time.monotonic() - start_time
        print(f"\nSession complete: {elapsed:.1f}s, {transcript_count} transcripts received")


async def stream_multiple(pcm_data: bytes, num_streams: int = 1):
    """Run multiple concurrent audio streams."""
    if num_streams == 1:
        await stream_audio(pcm_data)
    else:
        print(f"Starting {num_streams} concurrent streams...")
        await asyncio.gather(*(stream_audio(pcm_data) for _ in range(num_streams)))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--num-streams", type=int, default=1, help="Number of concurrent streams")
    args = parser.parse_args()

    print("Decoding MP3 to PCM...")
    pcm_data = decode_mp3_to_pcm(AUDIO_FILE)
    asyncio.run(stream_multiple(pcm_data, args.num_streams))


if __name__ == "__main__":
    main()
