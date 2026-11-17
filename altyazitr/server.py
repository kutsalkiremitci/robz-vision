"""OBS Tarayıcı Kaynağı için yerel sunucu.

http://127.0.0.1:<port>/  → altyazı sayfası (şeffaf, 1920x1080 tuval için)
http://127.0.0.1:<port>/sw.js → OBS uygulamadan önce açılırsa sayfanın saklanan kopyasını açar
ws://127.0.0.1:<port>/ws  → canlı altyazı/stil/maske güncellemeleri

aiohttp ayrı bir iş parçacığındaki asyncio döngüsünde çalışır; Qt tarafı AsyncBridge.submit
ile eşyordam gönderir. Yalnızca 127.0.0.1'e bağlanır (güvenlik duvarı uyarısı çıkmaz).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable

from aiohttp import WSMsgType, web

from altyazitr.config import StyleCfg
from altyazitr.paths import resource_dir

log = logging.getLogger(__name__)


class AsyncBridge:
    """Arka plan iş parçacığında tek bir asyncio döngüsü."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._t = threading.Thread(target=self._run, name="asyncio", daemon=True)

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self) -> None:
        self._t.start()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._t.join(2)


def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        r = g = b = 0
    return f"rgba({r},{g},{b},{max(0.0, min(1.0, alpha)):.3f})"


def style_vars(st: StyleCfg) -> dict[str, str]:
    """StyleCfg → sayfadaki CSS değişkenleri. Pikseller 1080p tuvale göre vh'ye çevrilir."""
    vh = lambda px: f"{px / 10.8:.3f}vh"  # noqa: E731
    shadow = f"0 {vh(st.outline_width * 0.8 + 1)} {vh(st.outline_width * 2 + 4)} rgba(0,0,0,.85)" if st.shadow else "none"
    return {
        "font": f"'{st.font_family}', 'Segoe UI', Arial, sans-serif",
        "size": vh(st.font_size),
        "weight": str(st.font_weight),
        "color": st.text_color,
        "stroke": vh(st.outline_width * 2),  # paint-order ile yarısı dışarıda kalır
        "stroke-color": st.outline_color,
        "shadow": shadow,
        "bg": _hex_rgba(st.bg_color, st.bg_opacity),
        "speaker": st.speaker_color,
        "bottom": f"{st.bottom_percent:.2f}vh",
        "maxw": f"{st.max_width_percent:.2f}vw",
        "fade": f"{max(0, st.fade_ms)}ms",
        "orig-size": f"{st.original_size_percent / 100:.2f}em",
        "orig-display": "block" if st.show_original else "none",
        "mask": _hex_rgba(st.mask_color, st.mask_opacity),
    }


class OverlayServer:
    def __init__(self, port: int = 8765, host: str = "127.0.0.1"):
        self.host = host
        self.port = port
        self.clients: set[web.WebSocketResponse] = set()
        # Yalnızca OBS'ten gelenler (tarayıcı kaynağının kimliğinde "OBS/<sürüm>" yazar). Durum rozeti
        # bunu sayar: test için açılan bir tarayıcı sekmesi "OBS bağlı" gösterip sorunu gizlemesin.
        self.obs_clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        self.on_clients_changed: Callable[[int], None] | None = None
        self.state: dict[str, dict] = {
            "style": {"type": "style", "vars": {}},
            "sub": {"type": "sub", "text": "", "speaker": None, "original": ""},
            "mask": {"type": "mask", "rect": None},
        }

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def start(self) -> None:
        web_dir = resource_dir() / "web"
        app = web.Application()

        async def index(_req):
            return web.FileResponse(web_dir / "overlay.html", headers={"Cache-Control": "no-store"})

        async def sw(_req):
            return web.FileResponse(web_dir / "sw.js", headers={
                "Cache-Control": "no-store", "Content-Type": "text/javascript; charset=utf-8"})

        app.router.add_get("/", index)
        app.router.add_get("/sw.js", sw)
        app.router.add_get("/ws", self._ws)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("OBS sunucusu: %s", self.url)

    async def stop(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    def _notify(self) -> None:
        if self.on_clients_changed:
            try:
                self.on_clients_changed(len(self.obs_clients))
            except Exception:
                log.debug("istemci sayısı bildirilemedi", exc_info=True)

    async def _ws(self, req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(req)
        self.clients.add(ws)
        if "OBS/" in req.headers.get("User-Agent", ""):
            self.obs_clients.add(ws)
        self._notify()
        try:
            for m in self.state.values():  # OBS yeniden başlasa da son durumu gönder
                await ws.send_str(json.dumps(m, ensure_ascii=False))
            async for msg in ws:
                if msg.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
            self.obs_clients.discard(ws)
            self._notify()
        return ws

    async def _broadcast(self, m: dict) -> None:
        data = json.dumps(m, ensure_ascii=False)
        dead = []
        for c in list(self.clients):
            try:
                await c.send_str(data)
            except Exception:
                dead.append(c)
        for c in dead:
            self.clients.discard(c)
            self.obs_clients.discard(c)

    # --- Qt tarafından AsyncBridge.submit ile çağrılır ----------------
    async def set_subtitle(self, text: str, speaker: str | None = None, original: str = "") -> None:
        self.state["sub"] = {"type": "sub", "text": text, "speaker": speaker, "original": original}
        await self._broadcast(self.state["sub"])

    async def set_style(self, vars_: dict[str, str]) -> None:
        self.state["style"] = {"type": "style", "vars": dict(vars_)}
        await self._broadcast(self.state["style"])

    async def set_mask(self, rect: list[float] | None) -> None:
        self.state["mask"] = {"type": "mask", "rect": rect}
        await self._broadcast(self.state["mask"])
