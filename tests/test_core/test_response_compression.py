"""Runtime contract for Starlette GZip response compression."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.middleware.gzip import GZipMiddleware


@pytest.mark.asyncio
async def test_only_responses_at_least_500_bytes_are_gzipped():
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=500)

    @app.get("/small")
    async def small_response():
        return {"value": "short"}

    @app.get("/large")
    async def large_response():
        return {"value": "x" * 600}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        large = await client.get("/large", headers={"Accept-Encoding": "gzip"})
        small = await client.get("/small", headers={"Accept-Encoding": "gzip"})

    assert large.status_code == 200
    assert large.headers["content-encoding"] == "gzip"
    assert small.status_code == 200
    assert "content-encoding" not in small.headers
