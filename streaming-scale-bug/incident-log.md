# Incident: Transcription connections drop under concurrent load

## Timeline

- **Mon 14:02 UTC**: First report — agent running 3 simultaneous calls
  noticed transcription dropping after ~30 seconds. Streams show "closed"
  in the UI.
- **Mon 14:10 UTC**: Confirmed with 5 concurrent streams in the Docker
  environment. Single stream works fine indefinitely.
- **Mon 14:15 UTC**: Checked transcription service logs — connections are
  being closed by the service due to "keepalive timeout."
- **Mon 14:25 UTC**: Tried reproducing on a local MacBook without Docker
  resource constraints — **cannot reproduce**. 5 streams run for 10+ minutes.
- **Mon 14:30 UTC**: Container metrics show CPU near its limit during
  failures. Memory is fine (~50MB of 512MB).
- **Mon 14:40 UTC**: Redis is healthy, <1ms response times.

## What we know

- 1-2 streams work perfectly in Docker
- 4-5 concurrent streams cause connections to drop within 30-60 seconds
- The transcription service closes connections due to keepalive timeout
- Cannot reproduce on a MacBook
- The transcription service itself seems healthy — it's our client that
  fails to maintain the connection
- Issue started when we scaled from 1-2 concurrent calls to 4-5 during
  peak hours

## Investigation notes

- The TranscriptionClient uses a threaded architecture — reader, writer,
  and keepalive threads. Pretty standard pattern for a WebSocket client
  wrapper
- The transcription service requires periodic keepalive messages. If we
  don't send them fast enough, it drops us.
- Tried adding logging — the keepalive thread is definitely running. But
  sometimes the keepalive messages seem to not get sent in time.

## Diagnostic endpoint

`GET /debug/info` — returns event loop lag, thread count, session stats.
