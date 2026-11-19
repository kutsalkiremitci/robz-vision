"""Testler ve uçtan uca deneme için 127.0.0.1'de küçük bir sahte "GitHub": son sürüm kaydı (API), sürüm dosyaları ve
SHA256SUMS.txt. Gerçek GitHub'a istek gönderilmez (bkz. conftest.py).

    gh = FakeGitHub(); gh.start()
    gh.publish("1.3.1", setup_bytes)          # kayıt + kurulum dosyası + SHA256SUMS.txt
    source = gh.source                          # altyazitr.updater.Source (http://127.0.0.1:<port>/…)
    gh.requests                                 # gelen istekler: (yol, başlıklar)
    gh.stop()
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from altyazitr.updater import SUMS_NAME, Source, setup_name


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.files: dict[str, bytes] = {}  # yol → içerik
        self.release: dict[str, Any] | None = None
        self.etag = '"e1"'
        self.api_status = 200  # 200 dışı: api_headers ile birlikte döner
        self.api_headers: dict[str, str] = {}
        self.api_delay = 0.0
        self.truncate: dict[str, int] = {}  # yol → bu kadar bayt gönderip bağlantıyı kes
        self.redirect: dict[str, str] = {}  # yol → Location
        self._srv: ThreadingHTTPServer | None = None
        self._th: threading.Thread | None = None

    # --- sunucu ---------------------------------------------------------------------------------------------
    @property
    def base(self) -> str:
        assert self._srv is not None
        return f"http://127.0.0.1:{self._srv.server_port}"

    @property
    def source(self) -> Source:
        return Source(api_url=f"{self.base}/api/latest", download_prefix=f"{self.base}/dl/",
                      releases_url=f"{self.base}/releases")

    def start(self) -> FakeGitHub:
        gh = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - http.server
                gh.requests.append((self.path, {k: v for k, v in self.headers.items()}))
                if self.path in gh.redirect:
                    self.send_response(302)
                    self.send_header("Location", gh.redirect[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/api/latest":
                    self._api()
                elif self.path in gh.files:
                    self._file(gh.files[self.path], gh.truncate.get(self.path))
                else:
                    self._send(404, b'{"message": "Not Found"}')

            def _api(self) -> None:
                if gh.api_delay:
                    time.sleep(gh.api_delay)
                if gh.api_status != 200:
                    self._send(gh.api_status, b'{"message": "API rate limit exceeded"}', gh.api_headers)
                    return
                if gh.release is None:
                    self._send(404, b'{"message": "Not Found"}')
                    return
                if self.headers.get("If-None-Match") == gh.etag:
                    self.send_response(304)
                    self.send_header("ETag", gh.etag)
                    self.end_headers()
                    return
                self._send(200, json.dumps(gh.release).encode(), {"ETag": gh.etag,
                                                                  "Content-Type": "application/json"})

            def _file(self, data: bytes, cut: int | None) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if cut is not None:
                    self.wfile.write(data[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    try:
                        import socket

                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                for i in range(0, len(data), 64 * 1024):
                    self.wfile.write(data[i:i + 64 * 1024])

            def _send(self, code: int, body: bytes, headers: dict[str, str] | None = None) -> None:
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def handle(self) -> None:
                try:
                    super().handle()
                except (ConnectionError, OSError):  # istemci bağlantıyı kesti (iptal, zaman aşımı)
                    pass

            def log_message(self, *args) -> None:
                pass

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._srv.daemon_threads = True
        self._th = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._th.start()
        return self

    def stop(self) -> None:
        if self._srv is not None:
            self._srv.shutdown()
            self._srv.server_close()
        if self._th is not None:
            self._th.join(5)

    # --- içerik ---------------------------------------------------------------------------------------------
    def publish(self, version: str, setup: bytes, *, notes: str = "- Yeni özellik", sums: str | None = None,
                size: int | None = None, api_digest: str | None = None, draft: bool = False,
                prerelease: bool = False, setup_url: str | None = None, with_setup: bool = True) -> dict[str, Any]:
        """Sürüm kaydını, kurulum dosyasını ve SHA256SUMS.txt'yi yayımlar (release.yml'nin ürettiği adlarla)."""
        tag = f"v{version}"
        name = setup_name(version)
        digest = hashlib.sha256(setup).hexdigest()
        portable = f"RobzVision-Portable-{version}-x64.zip"
        sums_text = sums if sums is not None else (f"{hashlib.sha256(b'zip').hexdigest()}  {portable}\n"
                                                   f"{digest}  {name}\n")
        sums_bytes = sums_text.encode()
        self.files[f"/dl/{tag}/{name}"] = setup
        self.files[f"/dl/{tag}/{SUMS_NAME}"] = sums_bytes
        assets = [{"name": portable, "size": 3, "browser_download_url": f"{self.base}/dl/{tag}/{portable}"},
                  {"name": SUMS_NAME, "size": len(sums_bytes),
                   "browser_download_url": f"{self.base}/dl/{tag}/{SUMS_NAME}",
                   "digest": "sha256:" + hashlib.sha256(sums_bytes).hexdigest()}]
        if with_setup:
            assets.append({"name": name, "size": len(setup) if size is None else size,
                           "browser_download_url": setup_url or f"{self.base}/dl/{tag}/{name}",
                           "digest": "sha256:" + (api_digest if api_digest is not None else digest)})
        self.release = {
            "tag_name": tag, "name": f"Robz Vision {version}", "draft": draft, "prerelease": prerelease,
            "html_url": f"{self.base}/releases/tag/{tag}",
            "body": ("## Hangi dosyayı indirmeliyim?\r\n\r\n| Dosya | Ne için |\r\n\r\n"
                     f"## Bu sürümde\r\n\r\n{notes}\r\n"),
            "assets": assets,
        }
        self.etag = f'W/"{version}-{digest[:12]}"'  # GitHub'ınki gibi zayıf ETag; kayıt değişince değişir
        return self.release

    def api_requests(self) -> list[dict[str, str]]:
        return [h for p, h in self.requests if p == "/api/latest"]

    def paths(self) -> list[str]:
        return [p for p, _h in self.requests]
