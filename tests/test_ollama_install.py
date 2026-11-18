"""ollama_install: Ollama kurulum dosyasının indirilmesi, doğrulanması ve sessiz kurulumu.

Ağ, gerçek kurulum ve kayıt defterine yazma yoktur: HTTP oturumu, subprocess.Popen ve kayıt okuyucu/yazıcı
sahtedir; bunların gerçekleri her testte engellenir (_guard). Yalnız signer_org gerçek Windows API'siyle denenir
(Python'un kendi imzalı python.exe'si üzerinde) ve iki test gerçek kayıt defterini yalnız okur.
"""

from __future__ import annotations

import errno
import hashlib
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import requests

import altyazitr.ollama_install as oi

BODY = bytes(range(256)) * 40  # 10.240 bayt; yanlış yerden birleştirilen parça SHA256'yı bozar
N = len(BODY)
MSG_NETWORK = "İnternet bağlantısı yok. Bağlantı gelince kaldığı yerden sürer."
MSG_DIGEST = "İndirilen Ollama dosyası bozuk çıktı ve silindi. Tekrar dene'ye basın."
MSG_SIGNATURE = "Ollama dosyasının imzası doğrulanamadı; kurulmadı."


@pytest.fixture(autouse=True)
def _guard(monkeypatch):
    """Hiçbir test gerçek ağa çıkmaz, kurulum başlatmaz, kayıt defterine yazmaz (sahteler bunları ezer)."""
    def forbidden(what):
        def f(*_a, **_k):
            raise AssertionError(f"testte gerçek {what} kullanılmamalı")
        return f
    monkeypatch.setattr(requests, "Session", forbidden("requests.Session"))
    monkeypatch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
    monkeypatch.setattr(oi, "_reg_set_dword", forbidden("kayıt defteri yazımı"))


@pytest.fixture
def dl(monkeypatch, tmp_path):
    """Küçük sahte kurulum dosyası: boyut ve SHA256 BODY'ye göre, imzalayan "Ollama Inc."."""
    d = tmp_path / "indirilenler"
    monkeypatch.setattr(oi, "DOWNLOAD_DIR", d)
    monkeypatch.setattr(oi, "SETUP_BYTES", N)
    monkeypatch.setattr(oi, "SETUP_SHA256", hashlib.sha256(BODY).hexdigest())
    monkeypatch.setattr(oi, "signer_org", lambda p: "Ollama Inc.")
    monkeypatch.setattr(oi, "_PROGRESS_EVERY", 0.0)  # her parçada ilerleme bildirilsin
    return d


def _target() -> Path:
    return oi._setup_file()


def _part() -> Path:
    t = _target()
    return t.with_name(t.name + ".part")


def _write_part(data: bytes) -> Path:
    p = _part()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


class RecEvent(threading.Event):
    """İptal olayı; wait() beklemez, istenen süreyi kaydeder (5/15/45 sn gerçekten beklenmez)."""

    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float | None] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.waits.append(timeout)
        return self.is_set()


class FakeResponse:
    def __init__(self, status: int, data: bytes = b"", headers: dict[str, str] | None = None, *,
                 chunk: int = 1000, fail_after: int | None = None) -> None:
        self.status_code = status
        self.data = data
        self.headers = headers or {}
        self.chunk = chunk
        self.fail_after = fail_after
        self.closed = False

    def iter_content(self, chunk_size: int = 1):
        sent = 0
        for i in range(0, len(self.data), self.chunk):
            if self.fail_after is not None and sent >= self.fail_after:
                raise requests.exceptions.ChunkedEncodingError("Connection broken: IncompleteRead")
            piece = self.data[i:i + self.chunk]
            sent += len(piece)
            yield piece

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """requests.Session.get yerine geçer. Range başlığına göre 206 (honor_range) ya da 200 döner.

    plan: sıradaki isteklerin davranışı. None normal; istisna → get() fırlatır; int → yalnız o durum kodu;
    ("kes", n) → n bayt gönderip bağlantı kopar; ("aralik", s) → 206 ama içerik istenenden değil s'den başlar."""

    def __init__(self, body: bytes = BODY, *, honor_range: bool = True, plan=()) -> None:
        self.body = body
        self.honor_range = honor_range
        self.plan = list(plan)
        self.requests: list[dict[str, str]] = []
        self.responses: list[FakeResponse] = []
        self.closed = False

    def get(self, url, headers=None, stream=False, timeout=None):
        assert url == oi.SETUP_URL and stream and timeout
        headers = dict(headers or {})
        self.requests.append(headers)
        action = self.plan.pop(0) if self.plan else None
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, int):
            resp = FakeResponse(action)
        else:
            fail_after = action[1] if isinstance(action, tuple) and action[0] == "kes" else None
            start = 0
            rng = headers.get("Range")
            if rng and self.honor_range:
                start = int(rng.removeprefix("bytes=").removesuffix("-"))
            if isinstance(action, tuple) and action[0] == "aralik":
                start = action[1]
            data, total = self.body[start:], len(self.body)
            if rng and self.honor_range:
                hdr = {"Content-Range": f"bytes {start}-{total - 1}/{total}", "Content-Length": str(len(data))}
                resp = FakeResponse(206, data, hdr, fail_after=fail_after)
            else:
                resp = FakeResponse(200, data, {"Content-Length": str(len(data))}, fail_after=fail_after)
        self.responses.append(resp)
        return resp

    def close(self) -> None:
        self.closed = True


def _download(session: FakeSession, cancel: threading.Event | None = None, seen: list | None = None) -> Path:
    def progress(done: int, total: int) -> None:
        if seen is not None:
            seen.append((done, total))
    return oi.download_setup(progress, cancel if cancel is not None else RecEvent(), session=session)


# --- sabitler --------------------------------------------------------------------------------------------------
def test_pinned_constants_are_consistent():
    assert oi.SETUP_URL == (f"https://github.com/ollama/ollama/releases/download/v{oi.OLLAMA_VERSION}"
                            "/OllamaSetup.exe")
    assert re.fullmatch(r"[0-9a-f]{64}", oi.SETUP_SHA256)
    assert 1_000_000_000 < oi.SETUP_BYTES < 4_000_000_000
    assert oi.SIGNER_ORG == "Ollama Inc."
    assert oi.UNINSTALL_KEY == (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                                r"\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1")
    assert oi.DOWNLOAD_DIR.name == "indirilenler" and oi.SETUP_LOG.name == "ollama-kurulum.log"
    assert oi.RETRY_DELAYS == (5.0, 15.0, 45.0)


def test_setup_error_lines():
    e = oi._err("Kısa.", "uzun açıklama", "network")
    assert str(e) == "Kısa.\nAyrıntı: uzun açıklama"
    assert (e.short, e.detail, e.kind) == ("Kısa.", "uzun açıklama", "network")
    assert oi.SetupError("Tek satır").detail == "" and oi.SetupError("x").kind == ""


# --- download_setup: sürdürme (206/200) ---------------------------------------------------------------------
def test_fresh_download_200(dl):
    s, ev, seen = FakeSession(), RecEvent(), []
    path = _download(s, ev, seen)
    assert path == _target() and path.parent == dl
    assert path.read_bytes() == BODY and not _part().exists()
    assert "Range" not in s.requests[0] and s.requests[0]["Accept-Encoding"] == "identity"
    assert seen[0] == (0, N) and seen[-1] == (N, N)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert all(r.closed for r in s.responses) and ev.waits == []


def test_resume_with_range_206(dl):
    _write_part(BODY[:3000])
    s, seen = FakeSession(), []
    path = _download(s, seen=seen)
    assert s.requests[0]["Range"] == "bytes=3000-" and s.responses[0].status_code == 206
    assert path.read_bytes() == BODY
    assert seen[0] == (3000, N) and seen[-1] == (N, N)


def test_server_ignoring_range_restarts_from_zero(dl):
    _write_part(b"\xff" * 3000)  # sunucu Range'i yok sayarsa eski parça kullanılmamalı
    s, seen = FakeSession(honor_range=False), []
    path = _download(s, seen=seen)
    assert s.requests[0]["Range"] == "bytes=3000-" and s.responses[0].status_code == 200
    assert path.read_bytes() == BODY
    assert (0, N) in seen


def test_unexpected_content_range_start_restarts(dl):
    _write_part(BODY[:3000])
    s, ev = FakeSession(plan=[("aralik", 2000)]), RecEvent()
    path = _download(s, ev)
    assert path.read_bytes() == BODY
    assert "Range" not in s.requests[1]  # tutarsız parça silindi, baştan
    assert ev.waits == [5.0]


def test_complete_part_is_finalized_without_request(dl):
    _write_part(BODY)  # önceki oturumda inmiş ama adı değiştirilmemiş
    s = FakeSession()
    assert _download(s).read_bytes() == BODY
    assert s.requests == []


# --- download_setup: iptal --------------------------------------------------------------------------------------
def test_cancel_keeps_part_and_resumes_later(dl):
    ev = RecEvent()

    def progress(done: int, total: int) -> None:
        if done >= 4000:
            ev.set()  # ör. çeviri başladı: indirme duraklar

    with pytest.raises(oi.SetupError) as ei:
        oi.download_setup(progress, ev, session=FakeSession())
    assert ei.value.kind == "cancel" and ei.value.short == "İptal edildi."
    have = _part().stat().st_size
    assert not _target().exists() and 4000 <= have < N
    assert _part().read_bytes() == BODY[:have]

    s2 = FakeSession()
    path = _download(s2)
    assert s2.requests[0]["Range"] == f"bytes={have}-"
    assert path.read_bytes() == BODY


def test_cancel_before_start_makes_no_request(dl):
    ev, s = RecEvent(), FakeSession()
    ev.set()
    with pytest.raises(oi.SetupError) as ei:
        _download(s, ev)
    assert ei.value.kind == "cancel" and s.requests == []


def test_cancel_during_retry_wait(dl):
    class CancelOnWait(RecEvent):
        def wait(self, timeout=None):
            super().wait(timeout)
            self.set()
            return True

    ev = CancelOnWait()
    s = FakeSession(plan=[requests.exceptions.ConnectionError("bağlantı yok")])
    with pytest.raises(oi.SetupError) as ei:
        _download(s, ev)
    assert ei.value.kind == "cancel" and ev.waits == [5.0] and len(s.requests) == 1


# --- download_setup: ağ hataları -----------------------------------------------------------------------------
def test_network_errors_retry_5_15_45_then_fail(dl):
    err = requests.exceptions.ConnectionError("getaddrinfo failed")
    s, ev = FakeSession(plan=[err] * 4), RecEvent()
    with pytest.raises(oi.SetupError) as ei:
        _download(s, ev)
    assert ev.waits == [5.0, 15.0, 45.0] and len(s.requests) == 4
    lines = str(ei.value).splitlines()
    assert lines[0] == MSG_NETWORK and lines[1].startswith("Ayrıntı: ") and "ConnectionError" in lines[1]
    assert ei.value.kind == "network" and ei.value.__cause__ is err


def test_network_error_then_success(dl):
    s, ev = FakeSession(plan=[requests.exceptions.ConnectTimeout("zaman aşımı")]), RecEvent()
    assert _download(s, ev).read_bytes() == BODY
    assert ev.waits == [5.0]


def test_dropped_connection_resumes_from_part(dl):
    s, ev = FakeSession(plan=[("kes", 4000)]), RecEvent()
    path = _download(s, ev)
    assert "Range" not in s.requests[0] and s.requests[1]["Range"] == "bytes=4000-"
    assert path.read_bytes() == BODY and ev.waits == [5.0]


def test_progress_renews_retry_budget(dl, monkeypatch):
    """Bağlantı sık kopsa da her denemede ilerleme varsa indirme vazgeçmeden sürer."""
    monkeypatch.setattr(oi, "_RESET_AFTER", 1)
    s, ev = FakeSession(plan=[("kes", 1000)] * 6), RecEvent()
    assert _download(s, ev).read_bytes() == BODY
    assert ev.waits == [5.0] * 6 and len(s.requests) == 7


@pytest.mark.parametrize("code", [500, 503, 429])
def test_server_errors_are_retried(dl, code):
    s, ev = FakeSession(plan=[code]), RecEvent()
    assert _download(s, ev).read_bytes() == BODY
    assert ev.waits == [5.0] and s.responses[0].closed


def test_http_404_is_not_retried(dl):
    s, ev = FakeSession(plan=[404]), RecEvent()
    with pytest.raises(oi.SetupError) as ei:
        _download(s, ev)
    assert ei.value.kind == "download" and ei.value.short == "Ollama indirilemedi." and "404" in ei.value.detail
    assert len(s.requests) == 1 and ev.waits == []


def test_disk_full_keeps_part(dl):
    _write_part(BODY[:3000])
    s, ev = FakeSession(plan=[OSError(errno.ENOSPC, "No space left on device")]), RecEvent()
    with pytest.raises(oi.SetupError) as ei:
        _download(s, ev)
    assert ei.value.kind == "disk"
    assert ei.value.short == f"Diskte yer kalmadı ({dl.drive}). Yer açıp Tekrar dene'ye basın."
    assert _part().read_bytes() == BODY[:3000] and ev.waits == []


# --- download_setup: boyut / SHA256 tutmazsa silinir -----------------------------------------------------------
def test_wrong_total_in_content_range_deletes_part(dl):
    _write_part(BODY[:3000])
    s = FakeSession(body=BODY + b"!")  # sunucudaki dosya bir bayt uzun
    with pytest.raises(oi.SetupError) as ei:
        _download(s)
    assert ei.value.kind == "digest" and ei.value.short == MSG_DIGEST
    assert not _part().exists() and not _target().exists()


def test_wrong_content_length_deletes_part(dl):
    s = FakeSession(body=BODY[:-1])
    with pytest.raises(oi.SetupError) as ei:
        _download(s)
    assert ei.value.kind == "digest" and "bayt" in ei.value.detail
    assert list(dl.iterdir()) == []


def test_sha256_mismatch_deletes_file(dl):
    s = FakeSession(body=bytes(reversed(BODY)))  # aynı boyut, farklı içerik
    with pytest.raises(oi.SetupError) as ei:
        _download(s)
    assert ei.value.kind == "digest" and ei.value.short == MSG_DIGEST and "SHA256" in ei.value.detail
    assert list(dl.iterdir()) == []


def test_existing_verified_file_is_reused(dl):
    t = _target()
    t.parent.mkdir(parents=True)
    t.write_bytes(BODY)
    s, seen = FakeSession(), []
    assert _download(s, seen=seen) == t
    assert s.requests == [] and seen[-1] == (N, N)


def test_existing_corrupt_file_is_downloaded_again(dl):
    t = _target()
    t.parent.mkdir(parents=True)
    t.write_bytes(b"\0" * N)
    s = FakeSession()
    assert _download(s).read_bytes() == BODY and len(s.requests) == 1


def test_existing_file_with_bad_signer_is_not_downloaded_again(dl, monkeypatch):
    monkeypatch.setattr(oi, "signer_org", lambda p: "Evil Inc.")
    t = _target()
    t.parent.mkdir(parents=True)
    t.write_bytes(BODY)
    s = FakeSession()
    with pytest.raises(oi.SetupError) as ei:
        _download(s)
    assert ei.value.kind == "signature" and s.requests == [] and not t.exists()


def test_stale_versions_are_removed(dl):
    dl.mkdir(parents=True)
    old = dl / "OllamaSetup-0.30.0.exe.part"
    old.write_bytes(b"x")
    other = dl / "baska-dosya.bin"
    other.write_bytes(b"x")
    _download(FakeSession())
    assert not old.exists() and other.exists()


def test_own_session_is_created_and_closed(dl, monkeypatch):
    made: list[FakeSession] = []

    def factory():
        made.append(FakeSession())
        return made[-1]

    monkeypatch.setattr(requests, "Session", factory)
    assert oi.download_setup(lambda d, t: None, RecEvent()).read_bytes() == BODY
    assert len(made) == 1 and made[0].closed


def test_progress_callback_error_does_not_break_download(dl):
    def broken(done: int, total: int) -> None:
        raise RuntimeError("arayüz hatası")

    assert oi.download_setup(broken, RecEvent(), session=FakeSession()).read_bytes() == BODY


def test_given_session_is_not_closed(dl):
    s = FakeSession()
    _download(s)
    assert not s.closed


def test_discard_download(dl):
    _write_part(b"y")
    _target().write_bytes(b"x")
    oi.discard_download()
    assert not _target().exists() and not _part().exists()
    oi.discard_download()  # dosya yoksa da hata vermez


# --- verify_setup ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("org,ok", [
    (None, False),
    ("Evil Inc.", False),
    ("Ollama Inc", False),
    ("Not Ollama Inc.", False),
    ("Ollama Inc.", True),
])
def test_verify_setup_signer(dl, monkeypatch, tmp_path, org, ok):
    monkeypatch.setattr(oi, "signer_org", lambda p: org)
    f = tmp_path / "OllamaSetup.exe"
    f.write_bytes(BODY)
    if ok:
        oi.verify_setup(f)
        assert f.exists()
        return
    with pytest.raises(oi.SetupError) as ei:
        oi.verify_setup(f)
    assert ei.value.kind == "signature" and ei.value.short == MSG_SIGNATURE
    assert oi.MANUAL_URL in ei.value.detail
    assert not f.exists()


def test_verify_setup_wrong_size_skips_signature_check(dl, monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(oi, "signer_org", lambda p: called.append(p) or "Ollama Inc.")
    f = tmp_path / "OllamaSetup.exe"
    f.write_bytes(BODY[:-1])
    with pytest.raises(oi.SetupError) as ei:
        oi.verify_setup(f)
    assert ei.value.kind == "digest" and ei.value.short == MSG_DIGEST and "bayt" in ei.value.detail
    assert not f.exists() and called == []


def test_verify_setup_wrong_sha256(dl, tmp_path):
    f = tmp_path / "OllamaSetup.exe"
    f.write_bytes(bytes(reversed(BODY)))
    with pytest.raises(oi.SetupError) as ei:
        oi.verify_setup(f)
    assert ei.value.kind == "digest" and hashlib.sha256(bytes(reversed(BODY))).hexdigest() in ei.value.detail
    assert not f.exists()


def test_verify_setup_missing_file(dl, tmp_path):
    with pytest.raises(oi.SetupError) as ei:
        oi.verify_setup(tmp_path / "yok.exe")
    assert ei.value.kind == "digest"


# --- signer_org (gerçek WinVerifyTrust) --------------------------------------------------------------------------
def _base_python() -> Path:
    return Path(getattr(sys, "_base_executable", None) or sys.executable)


def test_signer_org_of_python_exe():
    org = oi.signer_org(_base_python())
    if org is None:
        pytest.skip("Bu Python imzasız (python.org dışı bir kurulum)")
    assert org == "Python Software Foundation"


def test_signer_org_rejects_tampered_copy(tmp_path):
    """İmza bloğu sağlam ama içerik değişmiş: O= okunabilse de WinVerifyTrust geçersiz der → None."""
    exe = _base_python()
    if oi.signer_org(exe) is None:
        pytest.skip("Bu Python imzasız (python.org dışı bir kurulum)")
    data = bytearray(exe.read_bytes())
    data[len(data) // 3] ^= 0xFF
    bad = tmp_path / "python_bozuk.exe"
    bad.write_bytes(bytes(data))
    assert oi.signer_org(bad) is None


def test_signer_org_unsigned_and_missing(tmp_path):
    f = tmp_path / "imzasiz.exe"
    f.write_bytes(b"MZ" + b"\0" * 1000)
    assert oi.signer_org(f) is None
    assert oi.signer_org(tmp_path / "yok.exe") is None


# --- run_setup ------------------------------------------------------------------------------------------------
@pytest.fixture
def lad(monkeypatch, tmp_path):
    """Geçici %LOCALAPPDATA% (işaret dosyası ve varsayılan Ollama klasörü buraya)."""
    d = tmp_path / "LocalAppData"
    monkeypatch.setenv("LOCALAPPDATA", str(d))
    return d


def _fake_popen(monkeypatch, *, code: int = 0, wait_exc: BaseException | None = None,
                start_exc: BaseException | None = None, on_start=None) -> list:
    calls: list = []

    class P:
        def __init__(self, args, **kw):
            if start_exc is not None:
                raise start_exc
            if on_start is not None:
                on_start()
            self.args, self.kw, self.killed, self.timeout = args, kw, False, None
            calls.append(self)

        def wait(self, timeout=None):
            self.timeout = timeout
            if wait_exc is not None:
                raise wait_exc
            return code

        def kill(self):
            self.killed = True

        terminate = kill

    monkeypatch.setattr(subprocess, "Popen", P)
    return calls


@pytest.fixture
def setup_exe(tmp_path):
    exe = tmp_path / "indir" / "OllamaSetup-0.40.16.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    return exe


def test_run_setup_flags_marker_and_cleanup(monkeypatch, lad, tmp_path, setup_exe):
    marker = lad / "Ollama" / "upgraded"
    log_file = tmp_path / "Robz Vision Logs" / "ollama-kurulum.log"  # boşluklu yol tek argüman kalmalı
    marker_at_start = []
    calls = _fake_popen(monkeypatch, on_start=lambda: marker_at_start.append(marker.is_file()))
    oi.run_setup(setup_exe, log_file, timeout=123)
    (p,) = calls
    assert p.args == [str(setup_exe), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", f"/LOG={log_file}"]
    assert f'"/LOG={log_file}"' in subprocess.list2cmdline(p.args)  # Inno Setup tırnakları atar, tek parça kalır
    assert p.kw.get("stdin") is subprocess.DEVNULL
    assert p.kw.get("stdout") is subprocess.DEVNULL and p.kw.get("stderr") is subprocess.DEVNULL
    assert subprocess.PIPE not in p.kw.values()
    assert p.timeout == 123
    assert marker_at_start == [True]  # işaret kurulumdan ÖNCE oluşturulmalı
    assert log_file.parent.is_dir()
    assert not setup_exe.exists()  # başarıdan sonra kurulum dosyası silinir


def test_run_setup_existing_marker_is_fine(monkeypatch, lad, tmp_path, setup_exe):
    (lad / "Ollama").mkdir(parents=True)
    (lad / "Ollama" / "upgraded").write_bytes(b"")
    _fake_popen(monkeypatch)
    oi.run_setup(setup_exe, tmp_path / "k.log")


@pytest.mark.parametrize("code", [1, 4, 5])
def test_run_setup_nonzero_exit_code(monkeypatch, lad, tmp_path, setup_exe, code):
    log_file = tmp_path / "Logs" / "ollama-kurulum.log"
    _fake_popen(monkeypatch, code=code)
    with pytest.raises(oi.SetupError) as ei:
        oi.run_setup(setup_exe, log_file)
    lines = str(ei.value).splitlines()
    assert lines == ["Ollama kurulamadı.", f"Ayrıntı: Kurulum {code} koduyla bitti. Günlük: {log_file}"]
    assert ei.value.kind == "install"
    assert setup_exe.exists()  # Tekrar dene yeniden indirmesin


def test_run_setup_timeout_does_not_kill_installer(monkeypatch, lad, tmp_path, setup_exe):
    log_file = tmp_path / "ollama-kurulum.log"
    calls = _fake_popen(monkeypatch, wait_exc=subprocess.TimeoutExpired("OllamaSetup.exe", 900))
    with pytest.raises(oi.SetupError) as ei:
        oi.run_setup(setup_exe, log_file)
    assert ei.value.kind == "install" and ei.value.short == "Ollama kurulamadı."
    assert "15 dakika" in ei.value.detail and str(log_file) in ei.value.detail
    assert calls[0].killed is False and setup_exe.exists()


@pytest.mark.parametrize("winerror", [225, 1260, 4551, 5])
def test_run_setup_blocked_by_antivirus_or_policy(monkeypatch, lad, tmp_path, setup_exe, winerror):
    exc = OSError(0, "Engellendi", None, winerror)
    _fake_popen(monkeypatch, start_exc=exc)
    with pytest.raises(oi.SetupError) as ei:
        oi.run_setup(setup_exe, tmp_path / "k.log")
    assert ei.value.kind == "blocked" and ei.value.short == "Ollama kurulumu engellendi."
    assert ei.value.__cause__ is exc and setup_exe.exists()


def test_run_setup_missing_file_is_install_error(monkeypatch, lad, tmp_path, setup_exe):
    _fake_popen(monkeypatch, start_exc=FileNotFoundError(2, "Sistem belirtilen dosyayı bulamıyor"))
    with pytest.raises(oi.SetupError) as ei:
        oi.run_setup(setup_exe, tmp_path / "k.log")
    assert ei.value.kind == "install" and ei.value.short == "Ollama kurulamadı."


# --- error_from_os -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("winerror,word", [(225, "Antivirüs"), (1260, "ilke"), (4551, "ilke"), (5, "antivirüs")])
def test_error_from_os_blocked(winerror, word):
    e = oi.error_from_os(OSError(0, "Engellendi", None, winerror))
    lines = str(e).splitlines()
    assert lines[0] == "Ollama kurulumu engellendi." and lines[1].startswith("Ayrıntı: ")
    assert e.kind == "blocked" and word in e.detail
    assert oi.MANUAL_URL in e.detail and f"WinError {winerror}" in e.detail


@pytest.mark.parametrize("exc", [
    OSError(0, "Sistem belirtilen dosyayı bulamıyor", None, 2),
    FileNotFoundError(2, "yok"),
    OSError("başka bir sorun"),
])
def test_error_from_os_other(exc):
    e = oi.error_from_os(exc)
    assert e.kind == "install" and e.short == "Ollama kurulamadı."
    assert type(exc).__name__ in e.detail


# --- find_installed (sahte kayıt okuyucu) -------------------------------------------------------------------------
HKCU = ("HKCU", r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1")
HKLM = ("HKLM", HKCU[1])
WOW = ("HKLM", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
               r"\{44E83376-CE68-45EB-8FC1-393500EB558C}_is1")


def _fake_registry(monkeypatch, entries: dict) -> list:
    calls: list = []

    def read(hive, key):
        calls.append((hive, key))
        return entries.get((hive, key))

    monkeypatch.setattr(oi, "_reg_read", read)
    return calls


def _install(folder: Path) -> Path:
    folder.mkdir(parents=True)
    exe = folder / "ollama app.exe"
    exe.write_bytes(b"MZ")
    return exe


def test_find_installed_prefers_hkcu(monkeypatch, lad, tmp_path):
    user, machine = _install(tmp_path / "kullanici"), _install(tmp_path / "makine")
    unins = f'"{user.parent / "unins000.exe"}"'
    calls = _fake_registry(monkeypatch, {
        HKCU: {"InstallLocation": str(user.parent) + "\\", "DisplayVersion": "0.40.16", "UninstallString": unins},
        HKLM: {"InstallLocation": str(machine.parent), "DisplayVersion": "0.12.3"},
    })
    assert oi.find_installed() == oi.Installed(user, "0.40.16", unins)
    assert calls == [HKCU]


def test_find_installed_searches_hkcu_hklm_wow64_in_order(monkeypatch, lad, tmp_path):
    exe = _install(tmp_path / "x86")
    calls = _fake_registry(monkeypatch, {WOW: {"Inno Setup: App Path": str(exe.parent), "DisplayVersion": "0.12.3"}})
    assert oi.find_installed() == oi.Installed(exe, "0.12.3", "")
    assert calls == [HKCU, HKLM, WOW]


def test_find_installed_skips_stale_entry_and_uses_default_folder(monkeypatch, lad, tmp_path):
    _fake_registry(monkeypatch, {HKCU: {"InstallLocation": str(tmp_path / "silinmis"), "DisplayVersion": "0.9.0"}})
    exe = _install(lad / "Programs" / "Ollama")
    (exe.parent / "unins000.exe").write_bytes(b"MZ")
    assert oi.find_installed() == oi.Installed(exe, "", f'"{exe.parent / "unins000.exe"}"')


def test_find_installed_default_folder_without_uninstaller(monkeypatch, lad):
    _fake_registry(monkeypatch, {})
    exe = _install(lad / "Programs" / "Ollama")
    assert oi.find_installed() == oi.Installed(exe, "", "")


def test_find_installed_none(monkeypatch, lad):
    _fake_registry(monkeypatch, {HKLM: {"DisplayVersion": "0.40.16"}})  # yolsuz kayıt
    assert oi.find_installed() is None


def test_find_installed_real_registry_read_only():
    """Gerçek kayıt defterini yalnız okur; bu bilgisayarda Ollama varsa exe gerçekten oradadır."""
    got = oi.find_installed()
    assert got is None or got.app_exe.is_file()


def test_reg_read_missing_key():
    assert oi._reg_read("HKCU", r"Software\RobzVision\BoyleBirAnahtarYok_test") is None


# --- mark_installed_by_us (sahte kayıt yazıcısı) ------------------------------------------------------------------
def test_mark_installed_by_us_writes_hkcu_dword(monkeypatch):
    writes = []
    monkeypatch.setattr(oi, "_reg_set_dword", lambda *a: writes.append(a))
    oi.mark_installed_by_us()
    assert writes == [("HKCU", r"Software\RobzVision", "OllamaKuruldu", 1)]


def test_mark_installed_by_us_never_raises(monkeypatch):
    def denied(*_a):
        raise PermissionError(13, "Erişim engellendi")

    monkeypatch.setattr(oi, "_reg_set_dword", denied)
    oi.mark_installed_by_us()
