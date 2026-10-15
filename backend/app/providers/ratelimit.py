import asyncio
import time
from collections import deque


class RateLimiter:
    """Sliding-window limiter: at most `max_per_minute` acquisitions per 60 s. 0 disables it."""

    def __init__(self, max_per_minute: int, now=time.monotonic, sleep=asyncio.sleep):
        self.max = max_per_minute
        self._now = now
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if self.max <= 0:
            return
        async with self._lock:
            while True:
                t = self._now()
                while self._stamps and t - self._stamps[0] >= 60:
                    self._stamps.popleft()
                if len(self._stamps) < self.max:
                    self._stamps.append(t)
                    return
                await self._sleep(60 - (t - self._stamps[0]) + 0.05)