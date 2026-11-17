"""OBS sunucusu: Test düğmesinin altyazısı OBS kaynağına ulaşıyor mu, OBS istemcisi doğru sayılıyor mu,
OBS uygulamadan önce açılırsa sayfayı açacak service worker sunuluyor mu (ekran gerektirmez)."""

import asyncio
import json
import os
import socket
import time

import aiohttp
import pytest

from altyazitr.config import Settings
from altyazitr.server import AsyncBridge, OverlayServer

OBS_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/127.0.6533.120 OBS/32.2.2 Safari/537.36")
CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/131.0.0.0 Safari/537.36")


class _Overlay:
    """Ekran overlay'inin yerine: yalnızca son çağrıyı tutar."""

    def __init__(self):
        self.text = None

    def set_subtitle(self, text, speaker, original):
        self.text = text

    def clear(self):
        self.text = None

    def update(self):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    return app


@pytest.fixture
def live(qapp):
    from altyazitr.display import SubtitleDisplay

    bridge = AsyncBridge()
    bridge.start()
    server = OverlayServer(_free_port())
    counts: list[int] = []
    server.on_clients_changed = counts.append
    bridge.submit(server.start()).result(5)
    display = SubtitleDisplay(Settings(), bridge, server, _Overlay())
    try:
        yield server, display, counts
    finally:
        display._hide.stop()
        bridge.submit(server.stop()).result(5)
        bridge.stop()


def _ws_url(server: OverlayServer) -> str:
    return f"ws://127.0.0.1:{server.port}/ws"


async def _next_sub(ws) -> dict:
    while True:
        m = json.loads((await ws.receive(timeout=5)).data)
        if m["type"] == "sub":
            return m


def test_test_dugmesi_obs_kaynagina_ulasir(live):
    server, display, counts = live

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(_ws_url(server), headers={"User-Agent": OBS_UA}) as ws:
                first = await _next_sub(ws)  # bağlanınca son durum gelir
                assert first["text"] == ""
                assert len(server.obs_clients) == 1
                display.show_test()  # Görünüm/Ana sayfa "Test" düğmesinin çağırdığı yol
                m = await _next_sub(ws)
                assert "test altyazısıdır" in m["text"]
                assert m["original"].startswith("This is a test subtitle")

    asyncio.run(senaryo())
    end = time.monotonic() + 3
    while counts[-1] != 0 and time.monotonic() < end:  # ayrılma sunucu iş parçacığında işlenir
        time.sleep(0.02)
    assert counts[0] == 1 and counts[-1] == 0  # OBS bağlandı, sonra ayrıldı


def test_gec_baglanan_obs_gorunen_altyaziyi_alir(live):
    """OBS sayfası uygulamadan sonra bağlanırsa (yenileme, yeniden bağlanma) ekrandaki altyazıyı hemen alır."""
    server, display, _ = live
    display.show_test()

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(_ws_url(server), headers={"User-Agent": OBS_UA}) as ws:
                return await _next_sub(ws)

    assert "test altyazısıdır" in asyncio.run(senaryo())["text"]


def test_tarayici_sekmesi_obs_sayilmaz_ama_altyaziyi_alir(live):
    server, display, counts = live

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.ws_connect(_ws_url(server), headers={"User-Agent": CHROME_UA}) as ws:
                await _next_sub(ws)
                assert len(server.clients) == 1 and not server.obs_clients
                display.show_test()
                return await _next_sub(ws)

    assert "test altyazısıdır" in asyncio.run(senaryo())["text"]
    assert counts and not any(counts)  # durum rozeti "OBS bağlı" göstermez


def test_sayfa_ve_service_worker_sunuluyor(live):
    server, _, _ = live

    async def senaryo():
        async with aiohttp.ClientSession() as http:
            async with http.get(server.url) as r:
                page = await r.text()
            async with http.get(server.url + "sw.js") as r:
                return page, r.status, r.headers["Content-Type"], await r.text()

    page, status, ctype, sw = asyncio.run(senaryo())
    assert "navigator.serviceWorker.register('/sw.js')" in page
    assert status == 200 and ctype.startswith("text/javascript")
    assert "caches.match('/')" in sw
