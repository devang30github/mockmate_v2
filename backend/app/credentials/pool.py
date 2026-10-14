import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from app.providers.errors import NoCredentialAvailable

DEFAULT_COOLDOWN_S = 30.0


@dataclass
class Credential:
    id: str
    secret: str = field(repr=False)  # never appears in repr/logs
    enabled: bool = True
    cooldown_until: float = 0.0

    @property
    def last4(self) -> str:
        return self.secret[-4:]


class CredentialPool:
    """Cycle-based round-robin over one provider's keys.

    Order is insertion order. Within a cycle no key repeats while another
    eligible key is unused. A cycle ends when every currently eligible key has
    been used. Selection is serialized by a lock; Stage 7 replaces this with
    DB-persisted state and a row lock so it is also safe across workers.
    """

    def __init__(self, provider: str, now: Callable[[], float] = time.monotonic):
        self.provider = provider
        self._now = now
        self._creds: list[Credential] = []
        self._used: set[str] = set()
        self._last_index = -1
        self.cycle_no = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_csv(cls, provider: str, csv: str, **kwargs) -> "CredentialPool":
        pool = cls(provider, **kwargs)
        keys = [k.strip() for k in csv.split(",") if k.strip()]
        for i, secret in enumerate(keys, start=1):
            pool.add(f"{provider}_key_{i:02d}", secret)
        return pool

    # --- management -------------------------------------------------------
    def add(self, cred_id: str, secret: str) -> None:
        if any(c.id == cred_id for c in self._creds):
            raise ValueError(f"duplicate credential id: {cred_id}")
        self._creds.append(Credential(id=cred_id, secret=secret))  # unused this cycle

    def remove(self, cred_id: str) -> None:
        idx = next((i for i, c in enumerate(self._creds) if c.id == cred_id), None)
        if idx is None:
            raise KeyError(cred_id)
        del self._creds[idx]
        self._used.discard(cred_id)
        if idx <= self._last_index:
            self._last_index -= 1

    def _get(self, cred_id: str) -> Credential:
        for c in self._creds:
            if c.id == cred_id:
                return c
        raise KeyError(cred_id)

    def disable(self, cred_id: str) -> None:
        self._get(cred_id).enabled = False

    def enable(self, cred_id: str) -> None:
        self._get(cred_id).enabled = True

    def mark_rate_limited(self, cred_id: str, retry_after: float | None = None) -> None:
        self._get(cred_id).cooldown_until = self._now() + (retry_after or DEFAULT_COOLDOWN_S)

    def snapshot(self) -> list[dict]:
        """Safe for admin/UI: masked identifiers only, never secrets."""
        now = self._now()
        return [
            {
                "id": c.id,
                "last4": c.last4,
                "enabled": c.enabled,
                "cooling_down": c.cooldown_until > now,
                "used_in_cycle": c.id in self._used,
            }
            for c in self._creds
        ]

    # --- selection --------------------------------------------------------
    async def lease(self, exclude: frozenset[str] = frozenset()) -> Credential:
        async with self._lock:
            now = self._now()
            eligible = [
                c
                for c in self._creds
                if c.enabled and c.cooldown_until <= now and c.id not in exclude
            ]
            if not eligible:
                raise NoCredentialAvailable(f"no usable {self.provider} credential")
            candidates = [c for c in eligible if c.id not in self._used]
            if not candidates:
                self.cycle_no += 1
                self._used.clear()
                candidates = eligible
            order = {c.id: i for i, c in enumerate(self._creds)}
            after = [c for c in candidates if order[c.id] > self._last_index]
            chosen = (after or candidates)[0]
            self._used.add(chosen.id)
            self._last_index = order[chosen.id]
            return chosen