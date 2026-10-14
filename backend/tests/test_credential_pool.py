import asyncio
from collections import Counter

import pytest

from app.credentials.pool import CredentialPool
from app.providers.errors import NoCredentialAvailable

pytestmark = pytest.mark.anyio


def make_pool(n=3, clock=None):
    kwargs = {"now": clock} if clock else {}
    pool = CredentialPool("test", **kwargs)
    for i in range(n):
        pool.add("ABCDEFGH"[i], f"secret-value-{i}-xyz1")
    return pool


async def ids(pool, n, **kw):
    return [(await pool.lease(**kw)).id for _ in range(n)]


async def test_round_robin_order():
    assert await ids(make_pool(), 6) == ["A", "B", "C", "A", "B", "C"]


async def test_no_key_reused_within_a_cycle():
    first_cycle = await ids(make_pool(4), 4)
    assert sorted(first_cycle) == ["A", "B", "C", "D"]


async def test_disabled_key_is_skipped():
    pool = make_pool()
    pool.disable("B")
    assert await ids(pool, 4) == ["A", "C", "A", "C"]


async def test_cooling_key_is_skipped_then_gets_its_turn_before_cycle_restarts():
    t = [0.0]
    pool = make_pool(clock=lambda: t[0])
    pool.mark_rate_limited("B", retry_after=10)
    assert await ids(pool, 2) == ["A", "C"]
    t[0] = 11.0  # B recovers mid-cycle and is still unused
    assert (await pool.lease()).id == "B"


async def test_added_key_gets_a_turn_in_current_cycle():
    pool = make_pool(2)
    assert await ids(pool, 2) == ["A", "B"]
    pool.add("C", "secret-value-c-xyz1")
    assert (await pool.lease()).id == "C"


async def test_removing_last_used_key_continues_with_next():
    pool = make_pool()
    assert await ids(pool, 2) == ["A", "B"]
    pool.remove("B")
    assert (await pool.lease()).id == "C"


async def test_exclude_skips_already_tried_keys():
    assert (await make_pool().lease(exclude=frozenset({"A"}))).id == "B"


async def test_no_usable_credential_raises():
    pool = make_pool(2)
    pool.disable("A")
    pool.disable("B")
    with pytest.raises(NoCredentialAvailable):
        await pool.lease()


async def test_concurrent_leases_stay_balanced():
    pool = make_pool(3)
    leased = await asyncio.gather(*[pool.lease() for _ in range(30)])
    counts = Counter(c.id for c in leased)
    assert counts == {"A": 10, "B": 10, "C": 10}


async def test_secrets_never_appear_in_repr_or_snapshot():
    pool = make_pool()
    cred = await pool.lease()
    blob = repr(cred) + repr(pool.snapshot())
    assert "secret-value" not in blob


def test_from_csv_assigns_masked_ids():
    pool = CredentialPool.from_csv("sarvam", "k1-aaaa, k2-bbbb ,,k3-cccc")
    assert [s["id"] for s in pool.snapshot()] == [
        "sarvam_key_01",
        "sarvam_key_02",
        "sarvam_key_03",
    ]


def test_duplicate_id_rejected():
    pool = make_pool(1)
    with pytest.raises(ValueError):
        pool.add("A", "another-secret-zzzz")