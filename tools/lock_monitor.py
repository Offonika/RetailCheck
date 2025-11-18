"""
Inspect Redis locks `lock:run:*` with TTL info.

Usage:
    poetry run python tools/lock_monitor.py
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from redis.asyncio import Redis

from retailcheck.config import load_app_config


async def fetch_locks(redis: Redis) -> Sequence[tuple[str, int]]:
    keys = await redis.keys("lock:run:*")
    result = []
    for key in keys:
        ttl = await redis.pttl(key)
        name = key.decode() if isinstance(key, bytes) else key
        result.append((name, ttl))
    return sorted(result)


async def main() -> None:
    config = load_app_config()
    redis = Redis.from_url(config.redis.url)
    try:
        locks = await fetch_locks(redis)
    finally:
        await redis.close()
    if not locks:
        print("No lock:run:* keys in Redis.")
        return
    print("Active locks:")
    for name, ttl in locks:
        ttl_sec = ttl / 1000 if ttl and ttl > 0 else -1
        print(f"- {name} (ttl {ttl_sec:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
