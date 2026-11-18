"""ollama_runtime: yoklama, gizli başlatma, server.log ayrıştırma, model indirme ve hız testi.

Ağ kullanılmaz: sahte Ollama 127.0.0.1'de rastgele bir portta çalışır, gerçek Ollama'ya (11434) istek gitmez.
Kurulu Ollama araması (kayıt) her testte kapatılır; gereken testler kendi sahtesini verir."""

from __future__ import annotations

import json
import socket
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest

from altyazitr import ollama_runtime as R

_REAL_START_HIDDEN = R.start_hidden  # her testte sahtesiyle değiştirilir; kendi testleri bunu çağırır


# --- sahte Ollama -------------------------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # istemcinin kestiği bağlantıda yığın dökümü basmasın
        pass


class FakeOllama:
    """127.0.0.1'de sahte HTTP sunucusu. routes: (yöntem, yol) → işleyici(h, gövde); yolu olmayan istek 404 alır
    (Ollama olmayan bir program gibi). requests: gelen (yöntem, yol, gövde) kayıtları."""

    def __init__(self, port: int = 0):
        self.routes: dict = {}
        self.requests: list = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # Ollama gibi parçalı (chunked) akış için

            def _dispatch(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                fake.requests.append((method, self.path, body))
                self.close_connection = True
                route = fake.routes.get((method, self.path))
                if route is None:
                    send(self, 404, b"404 page not found", "text/plain")
                else:
                    route(self, body)

            def do_GET(self):
                self._dispatch("GET")

            def do_POST(self):
                self._dispatch("POST")

            def log_message(self, *args):
                pass

        self.server = _Server(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def send(h, status: int, body: bytes, ctype: str = "application/json") -> None:
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Connection", "close")
    h.end_headers()
    h.wfile.write(body)


def send_json(h, obj, status: int = 200) -> None:
    send(h, status, json.dumps(obj).encode())


def stream(h, messages, *, finish: bool = True, keep_alive: bool = False) -> None:
    """Ollama'nın /api/pull akışı gibi: her JSON satırı ayrı bir parça. finish=False: son parçayı yazmaz; işleyici
    dönünce bağlantı kopar (Ollama çökmüş gibi). keep_alive: "Connection: close" yazılmaz (Ollama'nın Go sunucusu
    gibi; istemcide soket bağlantı nesnesinde kalır)."""
    h.send_response(200)
    h.send_header("Content-Type", "application/x-ndjson")
    h.send_header("Transfer-Encoding", "chunked")
    if not keep_alive:
        h.send_header("Connection", "close")
    h.end_headers()
    for m in messages:
        data = (json.dumps(m) + "\n").encode()
        h.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
    if finish:
        h.wfile.write(b"0\r\n\r\n")


def ollama_routes(fake: FakeOllama, version: str = "0.40.16", models=("gemma4:12b",)) -> None:
    fake.routes[("GET", "/api/version")] = lambda h, b: send_json(h, {"version": version})
    fake.routes[("GET", "/api/tags")] = lambda h, b: send_json(
        h, {"models": [{"name": m, "model": m, "size": 1} for m in models]})


@pytest.fixture
def fake():
    srv = FakeOllama()
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch):
    """Kayıttaki gerçek kurulum okunmasın ve kimse gerçekten başlatılmasın."""
    monkeypatch.setattr(R, "_find_installed", lambda: None)
    monkeypatch.setattr(R, "start_hidden", lambda exe: pytest.fail("start_hidden beklenmiyordu"))


@contextmanager
def tcp_listener(*, accept_and_close: bool):
    """Ham TCP dinleyici. accept_and_close: bağlantıyı kabul edip yanıtsız kapatır (ör. arka ucu kapalı Docker
    port yönlendirmesi); değilse hiç kabul etmez: bağlanılır ama yanıt gelmez (açılmakta olan Ollama gibi)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    stop = threading.Event()

    def loop():
        srv.settimeout(0.05)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            conn.close()

    th = threading.Thread(target=loop, daemon=True) if accept_and_close else None
    if th:
        th.start()
    try:
        yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    finally:
        stop.set()
        if th:
            th.join(2)
        srv.close()


# --- sürüm --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("0.40.16", (0, 40, 16)),
    ("v0.12.3-rc1", (0, 12, 3)),
    ("ollama version is 0.5.7", (0, 5, 7)),
    ("0.30", (0, 30, 0)),
    ("0.0.0", (0, 0, 0)),
    ("", None),
    ("bilinmiyor", None),
    ("7", None),
])
def test_parse_version(text, expected):
    assert R.parse_version(text) == expected


def test_min_version_comparison():
    assert R.parse_version("0.30.5") >= R.MIN_VERSION > R.parse_version("0.30.4")
    assert R.parse_version("0.40.16") > R.MIN_VERSION > R.parse_version("0.12.3")


# --- adresler -----------------------------------------------------------------------------------------------
@pytest.fixture
def no_ollama_host(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(R, "_user_env", lambda name: "")


def test_candidate_urls_configured(no_ollama_host):
    assert R.candidate_urls("http://127.0.0.1:11434/") == ["http://127.0.0.1:11434"]
    assert R.candidate_urls("") == [R.DEFAULT_URL]
    assert R.candidate_urls("   ") == [R.DEFAULT_URL]
    assert R.candidate_urls("http://localhost:11434") == ["http://127.0.0.1:11434"]
    assert R.candidate_urls("127.0.0.1") == ["http://127.0.0.1:11434"]  # port yoksa 11434
    assert R.candidate_urls("http://127.0.0.1") == ["http://127.0.0.1:11434"]


@pytest.mark.parametrize("value, extra", [
    ("0.0.0.0", None),  # 127.0.0.1:11434 ile aynı: tekrar eklenmez
    ("0.0.0.0:11500", "http://127.0.0.1:11500"),
    (" '127.0.0.1:12000' ", "http://127.0.0.1:12000"),  # Ollama da boşluk ve tırnağı atar
    ("[::]:11434", None),
    (":11700", "http://127.0.0.1:11700"),
    ("::1", "http://[::1]:11434"),
    ("[::1]:11600", "http://[::1]:11600"),
    ("localhost:abc", None),  # geçersiz port: varsayılan
    ("192.168.1.20", "http://192.168.1.20:11434"),
    ("http://0.0.0.0", "http://127.0.0.1:80"),  # Ollama'nın kuralı: şemalı ve portsuz → 80
    ("https://example.com/ollama/", "https://example.com:443/ollama"),
    ("ollama.com", None),  # Ollama'nın bulut adresi: bu bilgisayardaki sunucu değil
    ("ftp://x:1", None),
])
def test_candidate_urls_ollama_host(no_ollama_host, monkeypatch, value, extra):
    monkeypatch.setenv("OLLAMA_HOST", value)
    assert R.candidate_urls("http://127.0.0.1:11434") == ["http://127.0.0.1:11434"] + ([extra] if extra else [])


def test_candidate_urls_reads_user_registry_too(no_ollama_host, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11999")
    monkeypatch.setattr(R, "_user_env", lambda name: "0.0.0.0:12345" if name == "OLLAMA_HOST" else "")
    assert R.candidate_urls("http://127.0.0.1:11434") == [
        "http://127.0.0.1:11434", "http://127.0.0.1:11999", "http://127.0.0.1:12345"]


def test_user_env_missing_value_is_empty():
    assert R._user_env("ROBZVISION_OLMAYAN_DEGISKEN_9F3A") == ""


def test_is_local():
    assert R._is_local("http://127.0.0.1:11434") and R._is_local("http://[::1]:11434")
    assert not R._is_local("http://192.168.1.20:11434")


# --- yoklama ------------------------------------------------------------------------------------------------
def test_probe_ok(fake, monkeypatch):
    ollama_routes(fake, models=("gemma4:12b", "gemma4:e4b"))
    installed = SimpleNamespace(app_exe=Path("ollama app.exe"), version="0.40.16", uninstall="")
    monkeypatch.setattr(R, "_find_installed", lambda: installed)
    p = R.probe(fake.url + "/")
    assert (p.url, p.api, p.version, p.other_on_port) == (fake.url, True, (0, 40, 16), False)
    assert p.models == ["gemma4:12b", "gemma4:e4b"]
    assert p.installed is installed


def test_probe_other_program_on_port(fake):
    fake.routes[("GET", "/")] = lambda h, b: send(h, 200, b"<html>Baska bir program</html>", "text/html")
    p = R.probe(fake.url)
    assert (p.api, p.version, p.other_on_port, p.models) == (False, None, True, [])


def test_probe_json_without_version_is_other(fake):
    fake.routes[("GET", "/api/version")] = lambda h, b: send_json(h, {"status": "ok"})
    assert R.probe(fake.url).other_on_port


def test_probe_old_ollama_without_version_endpoint(fake):
    fake.routes[("GET", "/")] = lambda h, b: send(h, 200, b"Ollama is running", "text/plain")
    fake.routes[("GET", "/api/tags")] = lambda h, b: send_json(h, {"models": [{"name": "llama3:latest"}]})
    p = R.probe(fake.url)
    assert (p.api, p.version, p.other_on_port, p.models) == (True, None, False, ["llama3:latest"])


def test_probe_connection_refused():
    p = R.probe(f"http://127.0.0.1:{_free_port()}", timeout=0.5)
    assert (p.api, p.version, p.other_on_port, p.models, p.installed) == (False, None, False, [], None)


def test_probe_closed_without_response_is_other():
    with tcp_listener(accept_and_close=True) as url:
        p = R.probe(url, timeout=1.0)
    assert p.other_on_port and not p.api


def test_probe_silent_listener_is_not_other():
    # bağlanıyor ama yanıt yok: Ollama açılırken (ekran kartlarını tararken) böyle; port başkasının sayılmaz
    with tcp_listener(accept_and_close=False) as url:
        p = R.probe(url, timeout=0.3)
    assert not p.api and not p.other_on_port


# --- başlatma ve bekleme ------------------------------------------------------------------------------------
def test_start_hidden_flags(monkeypatch, tmp_path):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(R.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(R, "_children", [])
    exe = tmp_path / "ollama app.exe"
    _REAL_START_HIDDEN(exe)
    args, kwargs = calls[0]
    assert args == [str(exe), "hidden", "--fast-startup"]
    assert not {"stdout", "stderr", "stdin"} & set(kwargs)  # çıktı borusu yok
    if sys.platform == "win32":
        assert kwargs["creationflags"] == R.DETACHED_PROCESS | R.CREATE_NO_WINDOW
        assert (R.DETACHED_PROCESS, R.CREATE_NO_WINDOW) == (subprocess.DETACHED_PROCESS, subprocess.CREATE_NO_WINDOW)


def test_start_hidden_missing_exe_raises_oserror(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "_children", [])
    with pytest.raises(OSError):
        _REAL_START_HIDDEN(tmp_path / "yok" / "ollama app.exe")


def test_wait_ready_returns_version(fake):
    ollama_routes(fake, version="0.31.2")
    assert R.wait_ready(fake.url, timeout=3) == (0, 31, 2)


def test_wait_ready_timeout(monkeypatch):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.2)
    port = _free_port()
    with pytest.raises(R.OllamaError) as ei:
        R.wait_ready(f"http://127.0.0.1:{port}", timeout=0.5)
    e = ei.value
    assert (e.kind, e.short) == ("start", "Ollama başlatılamadı.")
    assert str(port) in e.detail and "server.log" in e.detail
    assert str(e) == f"{e.short}\nAyrıntı: {e.detail}"


def test_wait_ready_other_program(fake):
    port = fake.url.rsplit(":", 1)[1]
    with pytest.raises(R.OllamaError) as ei:
        R.wait_ready(fake.url, timeout=0.3)
    assert (ei.value.kind, ei.value.short) == ("port", f"{port} portunu başka bir program kullanıyor.")


def test_wait_ready_cancel(monkeypatch):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.2)
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    t0 = time.monotonic()
    with pytest.raises(R.OllamaError) as ei:
        R.wait_ready(f"http://127.0.0.1:{_free_port()}", timeout=10, cancel=cancel)
    timer.cancel()
    assert (ei.value.kind, str(ei.value)) == ("cancel", "İptal edildi.")
    assert time.monotonic() - t0 < 2


def test_ensure_running_when_already_up(fake):
    ollama_routes(fake)
    assert R.ensure_running(fake.url, timeout=2) is True


def test_ensure_running_starts_hidden_and_waits(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.3)
    monkeypatch.setattr(R, "POLL_INTERVAL", 0.05)
    port = _free_port()
    exe = tmp_path / "ollama app.exe"
    monkeypatch.setattr(R, "_find_installed", lambda: SimpleNamespace(app_exe=exe, version="0.40.16", uninstall=""))
    started, servers = [], []

    def fake_start(app_exe):  # Ollama biraz sonra açılır
        started.append(app_exe)

        def later():
            time.sleep(0.4)
            srv = FakeOllama(port)
            ollama_routes(srv)
            servers.append(srv)

        threading.Thread(target=later, daemon=True).start()

    monkeypatch.setattr(R, "start_hidden", fake_start)
    try:
        assert R.ensure_running(f"http://127.0.0.1:{port}", timeout=5) is True
    finally:
        for srv in servers:
            srv.close()
    assert started == [exe]


def test_ensure_running_not_installed(monkeypatch):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.2)
    assert R.ensure_running(f"http://127.0.0.1:{_free_port()}", timeout=1) is False  # start_hidden çağrılmaz


def test_ensure_running_does_not_start_when_port_taken(fake, monkeypatch):
    monkeypatch.setattr(R, "_find_installed", lambda: SimpleNamespace(app_exe=Path("ollama app.exe")))
    assert R.ensure_running(fake.url, timeout=1) is False


def test_ensure_running_gives_up_after_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr(R, "_find_installed", lambda: SimpleNamespace(app_exe=tmp_path / "ollama app.exe"))
    started = []
    monkeypatch.setattr(R, "start_hidden", started.append)  # başlar ama hiç yanıt vermez
    t0 = time.monotonic()
    assert R.ensure_running(f"http://127.0.0.1:{_free_port()}", timeout=0.8) is False
    assert len(started) == 1 and time.monotonic() - t0 < 3


def test_ensure_running_start_error(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "PROBE_TIMEOUT", 0.2)
    monkeypatch.setattr(R, "_find_installed", lambda: SimpleNamespace(app_exe=tmp_path / "ollama app.exe"))

    def boom(exe):
        raise FileNotFoundError(exe)

    monkeypatch.setattr(R, "start_hidden", boom)
    assert R.ensure_running(f"http://127.0.0.1:{_free_port()}", timeout=1) is False


# --- server.log ---------------------------------------------------------------------------------------------
BOOT = (
    'time=2026-11-18T16:38:26.754+03:00 level=INFO source=routes.go:2474 msg="server config" '
    'env="map[CUDA_VISIBLE_DEVICES: OLLAMA_HOST:http://127.0.0.1:11434 OLLAMA_VULKAN:true]"\n'
    'time=2026-11-18T16:38:26.757+03:00 level=INFO source=routes.go:2536 '
    'msg="Listening on 127.0.0.1:11434 (version 0.40.16)"\n'
    'time=2026-11-18T16:38:26.758+03:00 level=INFO source=runner.go:60 msg="discovering available GPUs..."\n'
)
# Bu satırlar Ollama 0.40.16'nın gerçek günlüğünden (RTX 4070, tümleşik AMD kart atlanmış)
CUDA = (
    'time=2026-11-18T16:38:32.536+03:00 level=INFO source=runner.go:405 msg="dropping integrated GPU; to enable, '
    'set OLLAMA_IGPU_ENABLE=1" id=1 library=Vulkan compute=0.0 name=Vulkan1 description="AMD Radeon(TM) Graphics" '
    'pci_id=""\n'
    'time=2026-11-18T16:38:33.467+03:00 level=INFO source=types.go:32 msg="inference compute" id=0 filter_id=0 '
    'library=CUDA compute=8.9 name=CUDA0 description="NVIDIA GeForce RTX 4070" libdirs=ollama,cuda_v13 driver=13.5 '
    'pci_id=0000:01:00.0 type=discrete total="12.0 GiB" available="10.8 GiB"\n'
    'time=2026-11-18T16:38:33.467+03:00 level=INFO source=routes.go:2586 msg="vram-based default context" '
    'total_vram="12.0 GiB" default_num_ctx=4096\n'
    '[GIN] 2026/11/18 - 16:38:33 | 200 |       0s |       127.0.0.1 | GET      "/api/version"\n'
)
# Aşağıdakiler aynı biçimde (discover.LogDetails)
VULKAN = (
    'time=2026-11-18T10:00:05.000+03:00 level=INFO source=types.go:32 msg="inference compute" id=0 filter_id=0 '
    'library=Vulkan compute=0.0 name=Vulkan0 description="AMD Radeon RX 6700 XT" libdirs=ollama,vulkan driver=0.0 '
    'pci_id=0000:03:00.0 type=discrete total="12.0 GiB" available="457.0 MiB"\n'
)
CPU_ONLY = (
    'time=2026-11-18T10:00:05.000+03:00 level=INFO source=types.go:43 msg="inference compute" id=cpu library=cpu '
    'compute="" name=cpu description=cpu libdirs=ollama driver="" pci_id="" type="" total="31.9 GiB" '
    'available="20.4 GiB"\n'
)
IGPU = (
    'time=2026-11-18T10:00:05.000+03:00 level=INFO source=types.go:32 msg="inference compute" id=0 filter_id=0 '
    'library=Vulkan compute=0.0 name=Vulkan0 description="AMD Radeon(TM) 780M Graphics" libdirs=ollama,vulkan '
    'driver=0.0 pci_id=0000:c5:00.0 type=iGPU total="16.5 GiB" available="15.9 GiB"\n'
)
# Ollama 0.11 ve öncesi: name insan okunur, description ve type yok, library küçük harf
OLD_FORMAT = (
    'time=2025-03-01T10:00:00.000+03:00 level=INFO source=types.go:130 msg="inference compute" id=GPU-1a2b3c4d '
    'library=cuda variant=v12 compute=8.6 driver=12.8 name="NVIDIA GeForce RTX 3060" total="12.0 GiB" '
    'available="11.0 GiB"\n'
)
GIB = 1024**3


def _log(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "server.log"
    p.write_text(text, encoding="utf-8")
    return p


def test_inference_compute_cuda_real_line(tmp_path):
    assert R.inference_compute(_log(tmp_path, BOOT + CUDA)) == [
        R.Compute(library="CUDA", name="NVIDIA GeForce RTX 4070", kind="discrete", total=12 * GIB,
                  available=int(10.8 * GIB))]


def test_inference_compute_vulkan(tmp_path):
    [c] = R.inference_compute(_log(tmp_path, BOOT + VULKAN))
    assert (c.library, c.name, c.kind, c.total, c.available) == (
        "Vulkan", "AMD Radeon RX 6700 XT", "discrete", 12 * GIB, 457 * 1024**2)


def test_inference_compute_cpu_only(tmp_path):
    [c] = R.inference_compute(_log(tmp_path, BOOT + CPU_ONLY))
    assert (c.library, c.name, c.kind) == ("cpu", "cpu", "")
    assert c.total == int(31.9 * GIB)


def test_inference_compute_igpu(tmp_path):
    [c] = R.inference_compute(_log(tmp_path, BOOT + IGPU))
    assert (c.library, c.name, c.kind) == ("Vulkan", "AMD Radeon(TM) 780M Graphics", "iGPU")


def test_inference_compute_old_format(tmp_path):
    [c] = R.inference_compute(_log(tmp_path, BOOT + OLD_FORMAT))
    assert (c.library, c.name, c.kind, c.total) == ("CUDA", "NVIDIA GeForce RTX 3060", "", 12 * GIB)


def test_inference_compute_only_last_boot(tmp_path):
    two_cards = CUDA + VULKAN
    assert len(R.inference_compute(_log(tmp_path, BOOT + two_cards))) == 2
    # ikinci açılışta kart görünmedi (ör. sürücü bozuldu): yalnız onun cpu satırı sayılır
    [c] = R.inference_compute(_log(tmp_path, BOOT + two_cards + BOOT + CPU_ONLY))
    assert c.library == "cpu"
    # son açılış henüz aygıt yazmadıysa öncekiler de sayılmaz
    assert R.inference_compute(_log(tmp_path, BOOT + CUDA + BOOT)) == []


def test_inference_compute_unknown_format_is_empty(tmp_path):
    weird = 'time=x level=INFO msg="inference compute" aygit=gizli bellek=cok\n'
    assert R.inference_compute(_log(tmp_path, BOOT + weird)) == []
    assert R.inference_compute(_log(tmp_path, "")) == []
    assert R.inference_compute(tmp_path / "yok.log") == []
    assert R.inference_compute(tmp_path) == []  # klasör: okunamaz


def test_server_log_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert R.server_log_path() == tmp_path / "Ollama" / "server.log"


# --- model indirme ------------------------------------------------------------------------------------------
LAYERS = [
    {"status": "pulling manifest"},
    {"status": "pulling aaaa11112222", "digest": "sha256:aaaa", "total": 1000},
    {"status": "pulling aaaa11112222", "digest": "sha256:aaaa", "total": 1000, "completed": 400},
    {"status": "pulling aaaa11112222", "digest": "sha256:aaaa", "total": 1000, "completed": 1000},
    {"status": "pulling bbbb11112222", "digest": "sha256:bbbb", "total": 3000, "completed": 1500},
    {"status": "pulling bbbb11112222", "digest": "sha256:bbbb", "total": 3000, "completed": 3000},
    {"status": "pulling cccc11112222", "digest": "sha256:cccc", "total": 50, "completed": 50},  # zaten inmiş
    {"status": "verifying sha256 digest"},
    {"status": "writing manifest"},
    {"status": "success"},
]


def test_pull_sums_layers_by_digest(fake):
    fake.routes[("POST", "/api/pull")] = lambda h, b: stream(h, LAYERS)
    seen = []
    R.pull(fake.url, "gemma4:12b", lambda done, total, status: seen.append((done, total, status)), threading.Event())
    assert seen == [
        (0, 0, "Hazırlanıyor"),
        (0, 1000, "İndiriliyor"),
        (400, 1000, "İndiriliyor"),
        (1000, 1000, "İndiriliyor"),
        (2500, 4000, "İndiriliyor"),  # ikinci katman başlayınca sıfırlanmaz
        (4000, 4000, "İndiriliyor"),
        (4050, 4050, "İndiriliyor"),
        (4050, 4050, "Doğrulanıyor"),
        (4050, 4050, "Kaydediliyor"),
        (4050, 4050, "Tamamlandı"),
    ]
    method, path, body = fake.requests[-1]
    assert (method, path, json.loads(body)) == ("POST", "/api/pull", {"model": "gemma4:12b", "stream": True})


@pytest.mark.parametrize("keep_alive", [True, False])
def test_pull_cancel_hangs_up_waiting_stream(fake, keep_alive):
    hung_up = threading.Event()

    def route(h, b):
        # Ollama ilk satırlardan sonra susar (ör. büyük katmanın özetini doğruluyor). Go sunucusu gibi istemcinin
        # bağlantıyı bırakmasını (FIN) bekler: Ollama bunu görünce indirmeyi durdurur.
        stream(h, LAYERS[:3], finish=False, keep_alive=keep_alive)
        h.connection.settimeout(5)
        try:
            if h.connection.recv(1) == b"":
                hung_up.set()
        except OSError:
            hung_up.set()

    fake.routes[("POST", "/api/pull")] = route
    cancel = threading.Event()

    def progress(done, total, status):
        if done == 400:
            cancel.set()

    t0 = time.monotonic()
    with pytest.raises(R.PullError) as ei:
        R.pull(fake.url, "gemma4:12b", progress, cancel)
    assert (ei.value.kind, str(ei.value)) == ("cancel", "İptal edildi.")
    assert time.monotonic() - t0 < 1.5
    assert hung_up.wait(3)  # sunucu bağlantının bırakıldığını gördü


def test_pull_progress_error_propagates_and_hangs_up(fake):
    hung_up = threading.Event()

    def route(h, b):
        stream(h, LAYERS[:2], finish=False)
        h.connection.settimeout(5)
        try:
            h.connection.recv(1)
        except OSError:
            pass
        hung_up.set()

    fake.routes[("POST", "/api/pull")] = route

    def progress(done, total, status):
        if total:
            raise KeyError("arayüz hatası")

    with pytest.raises(KeyError):  # çağıranın hatası PullError'a çevrilmez
        R.pull(fake.url, "gemma4:12b", progress, threading.Event())
    assert hung_up.wait(3)


def test_pull_cancelled_before_start_sends_nothing(fake):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(R.PullError) as ei:
        R.pull(fake.url, "gemma4:12b", lambda *a: None, cancel)
    assert ei.value.kind == "cancel" and fake.requests == []


NETWORK = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer."
OUTDATED = "Ollama'nın sürümü bu model için eski."


@pytest.mark.parametrize("error, kind, short", [
    ("pull model manifest: 412: \n\nThe model you are attempting to pull requires a newer version of Ollama.\n\n"
     "Please download the latest version at:\n\n\thttps://ollama.com/download\n\n", "outdated", OUTDATED),
    ("model requires ollama version 0.41.0 or newer (current version is v0.40.16)", "outdated", OUTDATED),
    ("write C:\\Users\\oyuncu\\.ollama\\models\\blobs\\sha256-bb72-partial-3: There is not enough space on the "
     "disk.", "disk", "Diskte yer kalmadı (C:). Yer açıp Tekrar dene'ye basın."),
    ("write /root/.ollama/models/blobs/sha256-bb72-partial-3: no space left on device", "disk",
     "Diskte yer kalmadı. Yer açıp Tekrar dene'ye basın."),
    ("write D:\\modeller\\blobs\\sha256-bb72-partial-0: Diskte yeterli boş alan yok.", "disk",
     "Diskte yer kalmadı (D:). Yer açıp Tekrar dene'ye basın."),
    ('pull model manifest: Get "https://registry.ollama.ai/v2/library/gemma4/manifests/12b": dial tcp: lookup '
     "registry.ollama.ai: no such host", "network", NETWORK),
    ("max retries exceeded: unexpected EOF", "network", NETWORK),
    ("pull model manifest: file does not exist", "not_found", "'gemma4:12b' modeli bulunamadı."),
    ("garip bir hata", "", "Model indirilemedi."),
])
def test_pull_error_mapping(fake, error, kind, short):
    fake.routes[("POST", "/api/pull")] = lambda h, b: stream(h, [LAYERS[0], {"error": error}])
    with pytest.raises(R.PullError) as ei:
        R.pull(fake.url, "gemma4:12b", lambda *a: None, threading.Event())
    e = ei.value
    assert isinstance(e, RuntimeError)
    assert (e.kind, e.short) == (kind, short)
    assert str(e).startswith(f"{short}\nAyrıntı: ") and e.detail


def test_pull_http_error_before_stream(fake):
    fake.routes[("POST", "/api/pull")] = lambda h, b: send_json(h, {"error": "invalid model name"}, 400)
    with pytest.raises(R.PullError) as ei:
        R.pull(fake.url, "gemma4:??", lambda *a: None, threading.Event())
    assert (ei.value.kind, ei.value.short, ei.value.detail) == ("", "Model indirilemedi.", "invalid model name")


@pytest.mark.parametrize("finish", [True, False])
def test_pull_stream_ends_without_success(fake, finish):
    # Ollama işi bitirmeden kapandı: akış düzgün bitse de (finish) kopsa da ağ hatası (sonra sürdürülebilir)
    fake.routes[("POST", "/api/pull")] = lambda h, b: stream(h, LAYERS[:3], finish=finish)
    with pytest.raises(R.PullError) as ei:
        R.pull(fake.url, "gemma4:12b", lambda *a: None, threading.Event())
    assert (ei.value.kind, ei.value.short) == ("network", "Ollama'yla bağlantı koptu.")


def test_pull_ollama_not_running(monkeypatch):
    monkeypatch.setattr(R, "PULL_TIMEOUT", (0.3, 5.0))
    with pytest.raises(R.PullError) as ei:
        R.pull(f"http://127.0.0.1:{_free_port()}", "gemma4:12b", lambda *a: None, threading.Event())
    assert (ei.value.kind, ei.value.short) == ("network", "Ollama'ya bağlanılamadı.")


# --- ekran kartı payı ve hız testi --------------------------------------------------------------------------
def test_gpu_share(fake):
    fake.routes[("GET", "/api/ps")] = lambda h, b: send_json(h, {"models": [
        {"name": "gemma4:12b", "model": "gemma4:12b", "size": 8000, "size_vram": 6000},
        {"name": "llama3:latest", "model": "llama3:latest", "size": 100, "size_vram": 100},
        {"name": "bozuk:1b", "model": "bozuk:1b", "size": 0, "size_vram": 0},
    ]})
    assert R.gpu_share(fake.url, "gemma4:12b") == pytest.approx(0.75)
    assert R.gpu_share(fake.url, "llama3") == 1.0  # etiketsiz ad :latest sayılır
    assert R.gpu_share(fake.url, "gemma4:e4b") is None  # yüklü değil
    assert R.gpu_share(fake.url, "bozuk:1b") is None


def test_gpu_share_error_is_none(fake):
    fake.routes[("GET", "/api/ps")] = lambda h, b: send(h, 500, b"hata", "text/plain")
    assert R.gpu_share(fake.url, "gemma4:12b") is None


@pytest.mark.parametrize("median, verdict", [
    (170, "fast"), (600, "fast"), (601, "ok"), (1100, "ok"), (1101, "slow"), (2600, "slow")])
def test_speed_verdict_thresholds(median, verdict):
    assert R.Speed(median_ms=median, p90_ms=median + 100, gpu_share=1.0).verdict == verdict


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class _FakeTranslator:
    """Her translate saati verilen süre kadar ilerletir; None zaman aşımı demektir."""

    def __init__(self, clock, durations, prepare_error=None):
        self.clock, self.durations, self.prepare_error = clock, list(durations), prepare_error
        self.prepared = self.closed = False
        self.calls = []

    def prepare(self, status):
        if self.prepare_error:
            raise self.prepare_error
        self.prepared = True

    def translate(self, text, context=()):
        import requests

        self.calls.append((text, list(context)))
        d = self.durations.pop(0)
        if d is None:
            self.clock.now += R.SPEED_LINE_TIMEOUT_S
            raise requests.ReadTimeout("okuma zaman aşımı")
        self.clock.now += d
        return f"tr:{text}"

    def close(self):
        self.closed = True


@pytest.fixture
def speed_env(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(R, "_clock", clock)
    monkeypatch.setattr(R, "gpu_share", lambda url, model: 1.0)
    made = {}

    def install(translator):
        def create(cfg, glossary):
            made["cfg"], made["glossary"] = cfg, glossary
            return translator

        monkeypatch.setattr("altyazitr.translate.create_translator", create)

    return SimpleNamespace(clock=clock, made=made, install=install)


def test_speed_test_measures_with_real_engine_settings(speed_env):
    tr = _FakeTranslator(speed_env.clock, [0.2, 0.4, 0.3, 0.5, 0.1])
    speed_env.install(tr)
    sp = R.speed_test("http://localhost:11434", "gemma4:e4b", "gta6", ["a", "b", " ", "c", "d", "e"])
    assert sp == R.Speed(median_ms=300, p90_ms=460, gpu_share=1.0)  # %90: 400 + (500 - 400) * 0,6
    assert sp.verdict == "fast"
    cfg = speed_env.made["cfg"]
    assert (cfg.engine, cfg.ollama_url, cfg.ollama_model, cfg.profile) == (
        "ollama", "http://127.0.0.1:11434", "gemma4:e4b", "gta6")
    assert cfg.timeout_s == R.SPEED_LINE_TIMEOUT_S
    assert tr.prepared and tr.closed
    # işlem hattındaki gibi önceki satırlar (motorun kendi çevirisiyle) bağlam olarak gider
    assert tr.calls[0] == ("a", []) and tr.calls[2] == ("c", [("a", "tr:a"), ("b", "tr:b")])


def test_speed_test_timeout_ends_measurement_as_slow(speed_env):
    tr = _FakeTranslator(speed_env.clock, [0.3, None, 0.2])
    speed_env.install(tr)
    sp = R.speed_test(R.DEFAULT_URL, "gemma4:12b", "gta6", ["a", "b", "c"])
    assert len(tr.calls) == 2 and tr.closed
    assert sp.median_ms == round((300 + R.SPEED_LINE_TIMEOUT_S * 1000) / 2) and sp.verdict == "slow"


def test_speed_test_budget_skips_remaining_lines(speed_env, monkeypatch):
    monkeypatch.setattr(R, "SPEED_BUDGET_S", 2.0)
    tr = _FakeTranslator(speed_env.clock, [1.5, 1.5, 1.5, 1.5])
    speed_env.install(tr)
    assert R.speed_test(R.DEFAULT_URL, "gemma4:12b", "gta6", ["a", "b", "c", "d"]).median_ms == 1500
    assert len(tr.calls) == 2


def test_speed_test_prepare_error_propagates(speed_env):
    from altyazitr.translate import TranslatorError

    tr = _FakeTranslator(speed_env.clock, [], prepare_error=TranslatorError("'gemma4:12b' modeli yüklü değil."))
    speed_env.install(tr)
    with pytest.raises(TranslatorError):
        R.speed_test(R.DEFAULT_URL, "gemma4:12b", "gta6", ["a"])
    assert tr.closed and not tr.calls


def test_speed_test_needs_lines():
    with pytest.raises(ValueError):
        R.speed_test(R.DEFAULT_URL, "gemma4:12b", "gta6", ["", "  "])
