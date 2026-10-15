import pytest

from app.providers.ratelimit import RateLimiter

pytestmark = pytest.mark.anyio


async def test_blocks_until_the_window_frees():
    t = [0.0]
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)
        t[0] += s

    limiter = RateLimiter(2, now=lambda: t[0], sleep=fake_sleep)
    await limiter.acquire()
    await limiter.acquire()
    assert slept == []
    await limiter.acquire()
    assert len(slept) == 1 and 59 < slept[0] <= 61


async def test_zero_disables_limiting():
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    limiter = RateLimiter(0, sleep=fake_sleep)
    for _ in range(50):
        await limiter.acquire()
    assert slept == []