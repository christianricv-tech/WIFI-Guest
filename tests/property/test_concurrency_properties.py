"""Property 13: Concurrent validation atomicity.

Feature: aruba-voucher-backend, Property 13.

_For any_ valid unused voucher code C, if N concurrent requests (N >= 2) are made
to ``/radius/validate`` simultaneously, exactly one request receives
``{"result": "accept"}`` and all remaining N-1 requests receive
``{"result": "reject"}`` with ``reason = "consumed"``.

Validates: Requirements 4.3, 4.4
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st
from httpx import ASGITransport, AsyncClient

from tests.property import _harness as h

CODE = "AB3CD5EF7GH8"


async def _concurrency_scenario(n):
    await h.reset_tables()
    await h.insert_voucher(
        code=CODE,
        activation_mode="use-activated",
        duration_minutes=60,
    )

    app = h.build_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:

        async def one():
            resp = await client.post(
                "/radius/validate",
                json={"username": CODE, "password": CODE},
            )
            assert resp.status_code == 200, resp.text
            return resp.json()

        return await asyncio.gather(*[one() for _ in range(n)])


@given(n=st.integers(min_value=2, max_value=10))
@settings(max_examples=10, deadline=None)
def test_concurrent_validation_atomicity(n):
    results = h.run(_concurrency_scenario(n))

    accepts = [r for r in results if r.get("result") == "accept"]
    rejects = [r for r in results if r.get("result") == "reject"]

    assert len(accepts) == 1
    assert len(rejects) == n - 1
    assert all(r.get("reason") == "consumed" for r in rejects)
