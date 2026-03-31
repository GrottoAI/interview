"""Redis client for storing transcript history.

We use a sync Redis client here because our Redis operations are simple
key-value writes that complete in <1ms.
"""

import json
import logging
import os
import time
from typing import Any

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")

_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    """Return a shared Redis client (lazy-initialized, thread-safe)."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
    return _client


def store_transcript(session_id: str, transcript: dict[str, Any]) -> None:
    """Append a transcript entry to the session's history in Redis."""
    r = get_redis()
    entry = {
        "text": transcript.get("transcript", ""),
        "is_final": transcript.get("end_of_turn", False),
        "timestamp": time.time(),
        "words": transcript.get("words", []),
    }
    r.rpush(f"transcripts:{session_id}", json.dumps(entry))
    # Keep transcripts for 1 hour
    r.expire(f"transcripts:{session_id}", 3600)


def get_transcripts(session_id: str) -> list[dict[str, Any]]:
    """Retrieve all transcript entries for a session."""
    r = get_redis()
    raw = r.lrange(f"transcripts:{session_id}", 0, -1)
    return [json.loads(item) for item in raw]


def get_all_sessions() -> list[str]:
    """List all active transcript session IDs."""
    r = get_redis()
    keys = r.keys("transcripts:*")
    return [k.replace("transcripts:", "") for k in keys]
