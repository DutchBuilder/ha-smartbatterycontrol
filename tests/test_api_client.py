"""De API-client tegen een echte (kleine) HTTP-server: token, fouten en parameters."""
from __future__ import annotations

from datetime import date

import pytest
from aiohttp import web

from custom_components.smartbatterycontrol.api import SbcAuthError, SbcClient, SbcError


@pytest.mark.enable_socket
async def test_client(aiohttp_client, socket_enabled) -> None:
    seen = {}

    async def state(request: web.Request) -> web.Response:
        if request.headers.get("Authorization") != "Bearer sbc_goed":
            return web.json_response({"detail": "ongeldig API-token"}, status=401)
        return web.json_response({"batteries": []})

    async def calculation(request: web.Request) -> web.Response:
        seen["day"] = request.query["day"]
        return web.json_response({"totals": {"net_eur": 1.0}})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": "0.1.0"})

    async def broken(request: web.Request) -> web.Response:
        return web.json_response({}, status=500)

    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/plan/{battery}/calculation", calculation)
    app.router.add_get("/api/metrics", broken)
    server = await aiohttp_client(app)
    host, port = server.host, server.port

    good = SbcClient(server.session, host, port, "sbc_goed")
    assert await good.version() == "0.1.0"
    assert await good.state() == {"batteries": []}
    assert (await good.calculation("b1", date(2026, 10, 29)))["totals"]["net_eur"] == 1.0
    assert seen["day"] == "2026-10-29"
    with pytest.raises(SbcError, match="HTTP 500"):
        await good.metrics(12)
    with pytest.raises(SbcAuthError):
        await SbcClient(server.session, host, port, "sbc_fout").state()
    with pytest.raises(SbcError, match="niet bereikbaar"):
        await SbcClient(server.session, "127.0.0.1", 1).version()
