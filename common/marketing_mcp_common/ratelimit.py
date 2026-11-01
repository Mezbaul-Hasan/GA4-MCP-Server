"""Client-side rate limiting shared by all servers.

Some Google APIs enforce tiny project-wide quotas (for example 0.25 QPS = 25 requests per
100-second window). :class:`TokenBucket` smooths our own traffic so we rarely hit 429s,
while still allowing a short burst (an audit-style tool is typically 4–6 quick list calls).

The bucket is asyncio-friendly: ``await bucket.acquire()`` before each request. It is safe
to share one bucket across concurrent tool calls inside one server process. It cannot see
other processes (another teammate's server) — the HTTP retry/backoff path still handles
the 429s that slip through.
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class RateLimiter(Protocol):
    async def acquire(self) -> None: ...


class TokenBucket:
    """Token bucket: ``rate_per_sec`` refill, ``capacity`` burst.

    ``rate_per_sec <= 0`` disables waiting entirely (useful in tests).
    """

    def __init__(self, rate_per_sec: float, capacity: int, *, clock=time.monotonic, sleep=asyncio.sleep) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.rate = float(rate_per_sec)
        self.capacity = int(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock: asyncio.Lock | None = None
        self.waits = 0  # number of acquire() calls that had to sleep (for status/debug)
        self.total_wait_seconds = 0.0

    def _refill(self) -> None:
        now = self._clock()
        if self.rate > 0:
            self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def _get_lock(self) -> asyncio.Lock:
        # Created lazily so the bucket can be built outside a running loop.
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        if self.rate <= 0:
            return
        async with self._get_lock():
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return
            needed = 1 - self._tokens
            delay = needed / self.rate
            self.waits += 1
            self.total_wait_seconds += delay
            await self._sleep(delay)
            self._refill()
            self._tokens = max(0.0, self._tokens - 1)

    def status(self) -> dict[str, float | int]:
        return {
            "rate_per_sec": self.rate,
            "capacity": self.capacity,
            "tokens_available": round(self.tokens, 2),
            "waits": self.waits,
            "total_wait_seconds": round(self.total_wait_seconds, 1),
        }
