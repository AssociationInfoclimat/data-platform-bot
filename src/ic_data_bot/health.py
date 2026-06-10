from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReadyState:
    discord_ready: bool = False
    core_loaded: bool = False

    @property
    def ready(self) -> bool:
        return self.discord_ready and self.core_loaded


def liveness() -> tuple[int, str]:
    return 200, "ok"


def readiness(state: ReadyState) -> tuple[int, str]:
    return (200, "ready") if state.ready else (503, "not ready")


def make_health_app(state: ReadyState):  # pragma: no cover (câblage aiohttp mince)
    from aiohttp import web

    async def _healthz(_request):
        code, text = liveness()
        return web.Response(status=code, text=text)

    async def _readyz(_request):
        code, text = readiness(state)
        return web.Response(status=code, text=text)

    app = web.Application()
    app.add_routes([web.get("/healthz", _healthz), web.get("/readyz", _readyz)])
    return app
