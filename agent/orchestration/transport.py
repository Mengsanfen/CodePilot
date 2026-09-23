"""Redis Streams notifications with DB polling fallback: no dual-write data loss."""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)
STREAM = "codepilot:runs"
GROUP = "codepilot-workers"


def client():
    url = os.environ.get("CODEPILOT_REDIS_URL")
    if not url:
        return None
    from redis import Redis
    return Redis.from_url(url, decode_responses=True, socket_connect_timeout=2, socket_timeout=3)


def notify_run(run_id: str):
    try:
        connection = client()
        if connection:
            connection.xadd(STREAM, {"run_id": run_id}, maxlen=10000, approximate=True)
    except Exception:
        log.warning("Redis notification unavailable; durable database polling remains active")


def poll_notifications(consumer: str):
    try:
        connection = client()
        if not connection:
            return
        from redis.exceptions import ResponseError
        try:
            connection.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        reclaimed = connection.xautoclaim(STREAM, GROUP, consumer, 30000, count=100)
        for message_id, _ in reclaimed[1]:
            connection.xack(STREAM, GROUP, message_id)
        for _, messages in connection.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=100):
            for message_id, _ in messages:
                # ACK means wakeup received, NOT execution completed. DB claim owns work.
                connection.xack(STREAM, GROUP, message_id)
    except Exception:
        log.warning("Redis wakeup unavailable; using database queue")
