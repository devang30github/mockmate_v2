import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.credentials.pool import Credential, CredentialPool
from app.providers.errors import (
    AuthFailed,
    NoCredentialAvailable,
    PaymentRequired,
    ProviderError,
    RateLimited,
    TransientError,
)

log = logging.getLogger("mockmate.credentials")
T = TypeVar("T")

PAYMENT_COOLDOWN_S = 1800.0


def backoff_delay(attempt: int) -> float:
    return min(0.5 * 2 ** (attempt - 1), 4.0) * random.uniform(0.5, 1.5)


async def call_with_credentials(
    pool: CredentialPool,
    op: Callable[[Credential], Awaitable[T]],
    *,
    label: str,
    max_attempts: int,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run op with a leased credential; on a retryable failure try again, bounded.

    Rate limited: cool that key down and try another. Credits exhausted: park that key for
    a long time and try another. Rejected key: disable it and try another. Transient error:
    back off and retry. Anything else (a bad request) propagates immediately."""
    excluded: set[str] = set()
    last: ProviderError | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            cred = await pool.lease(exclude=frozenset(excluded))
        except NoCredentialAvailable:
            if last is not None:
                raise last from None
            raise
        try:
            return await op(cred)
        except RateLimited as e:
            log.warning("%s rate limited key=%s attempt=%d", label, cred.id, attempt)
            pool.mark_rate_limited(cred.id, e.retry_after)
            excluded.add(cred.id)
            last = e
        except PaymentRequired as e:
            log.error("%s credits or quota exhausted key=%s", label, cred.id)
            pool.mark_rate_limited(cred.id, PAYMENT_COOLDOWN_S)
            excluded.add(cred.id)
            last = e
        except AuthFailed as e:
            log.error("%s credential disabled after auth failure key=%s", label, cred.id)
            pool.disable(cred.id)
            excluded.add(cred.id)
            last = e
        except TransientError as e:
            log.warning("%s transient error key=%s attempt=%d error=%s", label, cred.id, attempt, e)
            last = e
            if attempt < max_attempts:
                await sleep(backoff_delay(attempt))
    assert last is not None
    raise last