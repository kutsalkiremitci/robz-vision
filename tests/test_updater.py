"""Güncelleme çekirdeği ve servisi (altyazitr.updater, altyazitr.update_service): sürüm karşılaştırma, son sürüm
denetimi (ETag/304, oran sınırı, hatalar sessiz), indirme ve doğrulama (boyut, SHA256, beklenmeyen adres, iptal, yarım
indirme), kurulum türü (taşınabilir sürüm kendini güncellemez), ayar yedeği ve göçü, kurulum komutu (süreç
başlatılmaz: Popen sahte), saatlik zamanlayıcı. Sürüm dosyalarını üreten release.yml ve kurulum betiği
(RobzVision.iss) güncelleyicinin beklediği adları, parametreleri ve ayar klasörlerine dokunmamayı korur.

Ağ yok: 127.0.0.1'de sahte "GitHub" (tests/fake_github.py); gerçek GitHub'a istek conftest'te engellidir. Kurulum dosyası
hiç çalıştırılmaz; tek örnek kilidi işlevleri sahtedir. Qt 'offscreen'; pencere açılmaz."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from altyazitr import __version__, config, updater
from altyazitr.config import Settings, load_settings, save_settings
from altyazitr.updater import (CheckResult, Release, Source, UpdateChecker, UpdateError, download_setup, is_newer,
                               parse_release, parse_version, setup_name)
from tests.fake_github import FakeGitHub

ROOT = Path(__file__).resolve().parents[1]
SETUP = os.urandom(300_000) + b"robz-vision-kurulum"


@pytest.fixture(autouse=True)
def _local_only(monkeypatch, tmp_path):
    """127.0.0.1 istekleri sistemin vekil sunucusuna gitmesin; indirme ve günlük klasörleri geçici."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(updater, "UPDATE_DIR", tmp_path / "guncelleme")
    monkeypatch.setattr(updater, "SETUP_LOG", tmp_path / "gunluk" / "guncelleme-kurulum.log")
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "ayarlar" / "ayarlar.json")


@pytest.fixture
def gh():
    server = FakeGitHub().start()
    yield server
    server.stop()


# --- sürüm ------------------------------------------------------------------------------------------------------
def test_version_order():
    assert parse_version("v1.3.1") == (1, 3, 1) and parse_version("1.10.0") == (1, 10, 0)
    assert is_newer("1.3.1", "1.3.0") and is_newer("1.10.0", "1.3.1") and is_newer("v2.0.0", "1.99.99")
    assert not is_newer("1.3.0", "1.3.0") and not is_newer("1.2.9", "1.3.0") and not is_newer("1.9.0", "1.10.0")
    for bad in ("1.4.0-beta", "1.4.0rc1", "1.4", "1.4.0.1", "v", "", None, "latest"):  # ön sürüm ve bozuk etiket
        assert parse_version(bad) is None, bad
        assert not is_newer(bad, "1.3.0")
    assert parse_version(__version__) is not None  # uygulamanın kendi sürümü de karşılaştırılabilir


def _release_json(gh: FakeGitHub, version: str = "1.3.1", **kw) -> dict:
    return gh.publish(version, SETUP, **kw)


def test_parse_release_ignores_drafts_prereleases_and_foreign_urls(gh):
    src = gh.source
    rel = parse_release(_release_json(gh, notes="- Güncelleme\n- <script>x</script>"), src)
    assert rel is not None and rel.version == "1.3.1" and rel.tag == "v1.3.1" and rel.installable
    assert rel.setup.name == "RobzVision-Setup-1.3.1-x64.exe" and rel.setup.size == len(SETUP)
    assert rel.setup.digest == hashlib.sha256(SETUP).hexdigest()
    assert rel.notes.startswith("- Güncelleme") and "Hangi dosyayı" not in rel.notes  # yalnız "Bu sürümde"
    assert rel.page_url == f"{gh.base}/releases/tag/v1.3.1"
    assert parse_release(_release_json(gh, draft=True), src) is None
    assert parse_release(_release_json(gh, prerelease=True), src) is None
    data = _release_json(gh)
    data["tag_name"] = "v1.4.0-beta"
    assert parse_release(data, src) is None
    # başka depodan ya da başka sunucudan adres: dosya yok sayılır (kendini güncelleme yok, elle indirme önerilir)
    for url in ("https://github.com/baskasi/robz-vision/releases/download/v1.3.1/RobzVision-Setup-1.3.1-x64.exe",
                "https://example.com/RobzVision-Setup-1.3.1-x64.exe",
                f"{gh.base}/dl/v1.3.0/RobzVision-Setup-1.3.1-x64.exe"):
        rel = parse_release(_release_json(gh, setup_url=url), src)
        assert rel is not None and rel.setup is None and not rel.installable, url
    data = _release_json(gh)
    data["html_url"] = "https://kotu.example/sayfa"
    assert parse_release(data, src).page_url == f"{gh.base}/releases/tag/v1.3.1"


def test_github_source_is_https_and_this_repo():
    assert updater.API_URL == "https://api.github.com/repos/kutsalkiremitci/robz-vision/releases/latest"
    assert updater.DOWNLOAD_PREFIX == "https://github.com/kutsalkiremitci/robz-vision/releases/download/"
    assert updater.GITHUB.scheme == "https"
    assert updater.user_agent() == f"RobzVision/{__version__}"


# --- denetim ----------------------------------------------------------------------------------------------------
def test_check_uses_etag_and_skips_body_on_304(gh):
    _release_json(gh)
    chk = UpdateChecker(gh.source)
    first = chk.check()
    assert first.status == "ok" and first.release.version == "1.3.1" and chk.etag == gh.etag
    second = chk.check()
    assert second.status == "same" and second.release is first.release  # 304: gövde yok, önceki sürüm
    hdrs = gh.api_requests()
    assert len(hdrs) == 2 and "If-None-Match" not in hdrs[0] and hdrs[1]["If-None-Match"] == gh.etag
    for h in hdrs:  # genel ad, kişisel bilgi yok
        assert h["User-Agent"] == f"RobzVision/{__version__}"
        assert "Authorization" not in h and "Cookie" not in h
    _release_json(gh, "1.3.2")  # yeni sürüm: ETag değişti → tam yanıt
    third = chk.check()
    assert third.status == "ok" and third.release.version == "1.3.2"


def test_check_rate_limit_and_errors_are_quiet(gh, monkeypatch):
    _release_json(gh)
    chk = UpdateChecker(gh.source)
    now = time.time()
    gh.api_status, gh.api_headers = 403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(now + 5400))}
    res = chk.check()
    assert res.status == "limited" and 5300 <= res.retry_after <= 5500
    gh.api_status, gh.api_headers = 429, {"Retry-After": "7200"}
    assert chk.check().retry_after == 7200
    gh.api_status, gh.api_headers = 500, {}
    assert chk.check().status == "error"
    gh.api_status = 200
    gh.release = None
    assert chk.check().status == "none"
    # zaman aşımı (sunucu yanıt vermiyor) ve kapalı port: hata fırlamaz
    monkeypatch.setattr(updater, "API_TIMEOUT", 0.3)
    _release_json(gh)
    gh.api_delay = 1.5
    t0 = time.monotonic()
    res = UpdateChecker(gh.source).check()
    assert res.status == "error" and time.monotonic() - t0 < 1.4
    gh.api_delay = 0
    closed = Source(api_url="http://127.0.0.1:9/api/latest", download_prefix="http://127.0.0.1:9/dl/",
                    releases_url="http://127.0.0.1:9/releases")
    assert UpdateChecker(closed).check().status == "error"


def test_check_rejects_scheme_downgrade_on_redirect(gh):
    _release_json(gh)
    gh.redirect["/api/latest"] = "https://127.0.0.1:1/api/latest"  # kaynak http: başka şemaya gidilmez
    res = UpdateChecker(gh.source).check()
    assert res.status == "error" and "Güvensiz" in res.error
    https = Source()
    assert updater._same_scheme("https://objects.githubusercontent.com/x", https)
    assert not updater._same_scheme("http://github.com/x", https)


def test_retry_after_headers():
    now = 1_000_000.0
    assert updater.retry_after({"Retry-After": "120"}, now) == 120
    assert updater.retry_after({"Retry-After": "5"}, now) == updater.MIN_RETRY_S
    assert updater.retry_after({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(now + 900))}, now) == 900
    assert updater.retry_after({"X-RateLimit-Remaining": "3", "X-RateLimit-Reset": str(int(now + 900))}, now) is None
    assert updater.retry_after({"Retry-After": str(10 ** 9)}, now) == updater.MAX_RETRY_S
    assert updater.retry_after({}, now) is None


# --- indirme ve doğrulama ---------------------------------------------------------------------------------------
def _rel(gh: FakeGitHub, **kw) -> Release:
    rel = parse_release(_release_json(gh, **kw), gh.source)
    assert rel is not None
    return rel


def test_download_verifies_and_uses_a_fresh_folder(gh, tmp_path):
    rel = _rel(gh)
    folder = tmp_path / "guncelleme" / "1.3.1"
    folder.mkdir(parents=True)
    (folder / "eski.part").write_bytes(b"yarim")  # önceki denemeden kalan
    seen: list[tuple[int, int]] = []
    path, sha = download_setup(rel, lambda d, t: seen.append((d, t)), folder=folder, source=gh.source)
    assert path == folder / setup_name("1.3.1") and path.read_bytes() == SETUP
    assert sha == hashlib.sha256(SETUP).hexdigest()
    assert sorted(p.name for p in folder.iterdir()) == ["RobzVision-Setup-1.3.1-x64.exe", "SHA256SUMS.txt"]
    assert seen and seen[-1] == (len(SETUP), len(SETUP))
    for _p, h in gh.requests[1:]:
        assert "Range" not in h  # yarım indirme sürdürülmez, baştan alınır


@pytest.mark.parametrize("case", ["sums", "size", "api_digest"])
def test_download_mismatch_deletes_and_never_runs(gh, tmp_path, monkeypatch, case):
    """Boyut ya da özet tutmazsa dosya silinir; hiçbir süreç başlatılmaz."""
    started: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    if case == "sums":
        rel = _rel(gh, sums=f"{'0' * 64}  {setup_name('1.3.1')}\n", api_digest="")
    elif case == "size":
        rel = _rel(gh, size=len(SETUP) + 10)
    else:
        rel = _rel(gh, api_digest="1" * 64)
    folder = tmp_path / "g"
    with pytest.raises(UpdateError) as ei:
        download_setup(rel, folder=folder, source=gh.source)
    assert ei.value.kind == "digest" and "doğrulanamadı" in ei.value.short
    assert not any(p.suffix in (".exe", ".part") for p in folder.iterdir())
    if case == "api_digest":  # iki kaynak uyuşmuyor: kurulum dosyası hiç istenmez
        assert f"/dl/v1.3.1/{setup_name('1.3.1')}" not in gh.paths()
    assert started == []


def test_download_rejects_unexpected_url_without_request(gh, tmp_path):
    rel = _rel(gh)
    other = Release(rel.version, rel.tag, rel.page_url, rel.notes,
                    updater.Asset(rel.setup.name, "http://127.0.0.1:1/baska/x.exe", rel.setup.size), rel.sums)
    before = len(gh.requests)
    with pytest.raises(UpdateError) as ei:
        download_setup(other, folder=tmp_path / "g", source=gh.source)
    assert ei.value.kind == "download" and len(gh.requests) == before
    with pytest.raises(UpdateError) as ei:  # GitHub kaynağında http adres de reddedilir
        download_setup(Release("1.3.1", "v1.3.1", "", "", updater.Asset(
            setup_name("1.3.1"), "http://github.com/kutsalkiremitci/robz-vision/releases/download/v1.3.1/"
            + setup_name("1.3.1"), 10), rel.sums), folder=tmp_path / "h")
    assert ei.value.kind == "download"


def test_download_missing_cancel_and_cut_connection(gh, tmp_path):
    rel = _rel(gh, with_setup=False)
    with pytest.raises(UpdateError) as ei:
        download_setup(rel, folder=tmp_path / "a", source=gh.source)
    assert ei.value.kind == "missing"
    rel = _rel(gh)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UpdateError) as ei:
        download_setup(rel, cancel=cancel, folder=tmp_path / "b", source=gh.source)
    assert ei.value.kind == "cancel" and not list((tmp_path / "b").glob("*.exe*"))
    gh.truncate[f"/dl/v1.3.1/{setup_name('1.3.1')}"] = 100_000  # bağlantı yarıda kopar
    with pytest.raises(UpdateError) as ei:
        download_setup(rel, folder=tmp_path / "c", source=gh.source)
    assert ei.value.kind in ("network", "download") and not list((tmp_path / "c").glob("*.exe*"))
    gh.truncate.clear()  # sonraki deneme baştan ve başarılı
    path, _sha = download_setup(rel, folder=tmp_path / "c", source=gh.source)
    assert path.read_bytes() == SETUP


def test_verify_file_and_sums_parser(tmp_path):
    p = tmp_path / "x.exe"
    p.write_bytes(b"abc")
    sha = hashlib.sha256(b"abc").hexdigest()
    updater.verify_file(p, 3, sha)
    with pytest.raises(UpdateError):
        updater.verify_file(p, 3, "0" * 64)
    assert not p.exists()  # tutmayan dosya silinir
    text = f"{sha}  a.exe\n{'A' * 64} *b.zip\nbozuk satır\n"
    assert updater.parse_sums(text) == {"a.exe": sha, "b.zip": "a" * 64}


# --- kurulum türü ve başlatma -----------------------------------------------------------------------------------
def test_install_kind(tmp_path):
    exe = tmp_path / "Robz Vision" / "RobzVision.exe"
    exe.parent.mkdir()
    loc = {"HKCU": str(exe.parent) + "\\", "HKLM": None}
    assert updater.install_kind(exe, frozen=False, location=loc.get) == "source"
    assert updater.install_kind(exe, frozen=True, location=loc.get) == "user"
    assert updater.install_kind(exe, frozen=True, location={"HKLM": str(exe.parent)}.get) == "all_users"
    assert updater.install_kind(exe, frozen=True, location={"HKCU": str(tmp_path / "baska")}.get) == "portable"
    assert updater.install_kind(exe, frozen=True, location=lambda _h: None) == "portable"


def test_setup_command_and_launch_without_running(tmp_path, monkeypatch):
    setup = tmp_path / "Kullanıcı Adı" / setup_name("1.3.1")
    log_file = tmp_path / "gunluk" / "kurulum.log"
    args = updater.setup_command(setup, 4321, log_file)
    assert args[0] == str(setup) and args[-2:] == ["/BEKLE=4321", f"/LOG={log_file}"]
    assert {"/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOCANCEL", "/GUNCELLEME"} <= set(args)
    assert "/NORESTARTAPPLICATIONS" in args  # yeniden açmayı kurulumun [Run] satırı yapar (iki kez açılmasın)
    assert updater.setup_command(setup, 1)[-1] == f"/LOG={updater.SETUP_LOG}"  # varsayılan çağrı anında okunur
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "x")

    def fake_popen(argv, **kw):
        calls.append((argv, kw))
        if len(calls) == 1:
            raise PermissionError(5, "iş nesnesi kopmaya izin vermiyor")
        return object()

    updater.launch_setup(setup, 4321, log_file, popen=fake_popen)
    assert len(calls) == 2 and all(a == args for a, _ in calls)  # liste: kabuk ve kaçış yok
    first, second = calls[0][1], calls[1][1]
    assert first["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert not second["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert second["creationflags"] & subprocess.DETACHED_PROCESS and "shell" not in second
    assert second["cwd"] == str(setup.parent) and "_PYI_APPLICATION_HOME_DIR" not in second["env"]
    assert second["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert log_file.parent.is_dir()

    def blocked(argv, **kw):
        raise OSError(22, "virüs", None, 225)

    with pytest.raises(UpdateError) as ei:
        updater.launch_setup(setup, 1, log_file, popen=blocked)
    assert ei.value.kind == "blocked"


# --- ayarlar: yedek ve güncellemeden sonra okunma ----------------------------------------------------------------
def test_backup_keeps_last_three(tmp_path):
    f = tmp_path / "ayarlar.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    base = time.time() - 1000
    for i, v in enumerate(("1.0.0", "1.1.0", "1.2.0", "1.3.0")):
        p = updater.backup_settings(v, f)
        assert p == tmp_path / f"ayarlar.yedek-{v}.json"
        os.utime(p, (base + i, base + i))  # sıra: alınış zamanı
    names = sorted(p.name for p in tmp_path.glob("ayarlar.yedek-*.json"))
    assert names == ["ayarlar.yedek-1.1.0.json", "ayarlar.yedek-1.2.0.json", "ayarlar.yedek-1.3.0.json"]
    assert (tmp_path / "ayarlar.yedek-1.3.0.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert f.read_text(encoding="utf-8") == '{"a": 1}'  # asıl dosya değişmez
    assert updater.backup_settings("1.3.0", tmp_path / "yok.json") is None


def test_old_settings_survive_the_update_path(tmp_path):
    """1.2.1'den (ayar biçimi 5) güncelleyen kullanıcının dosyası: yedek birebir; yeni sürüm dosyayı okur, kendi
    seçimleri korunur, güncelleme ayarları varsayılanla gelir (dosyada yok)."""
    old = {"settings_version": 5, "translate": {"engine": "gemini", "gemini_key": "anahtar", "glossary": [["Vice City",
           "Vice City"]]}, "capture": {"rect": [10, 20, 300, 80], "fps": 15}, "hotkeys": {"toggle": "Ctrl+F9"},
           "style": {"font_size": 52}, "tour_seen": True, "first_run": False}
    config.SETTINGS_FILE.parent.mkdir(parents=True)
    config.SETTINGS_FILE.write_text(json.dumps(old), encoding="utf-8")
    backup = updater.backup_settings("1.2.1")
    assert json.loads(backup.read_text(encoding="utf-8")) == old
    s = load_settings()
    assert s.translate.engine == "gemini" and s.translate.gemini_key == "anahtar"
    assert s.capture.rect == [10, 20, 300, 80] and s.capture.fps == 15 and s.hotkeys.toggle == "Ctrl+F9"
    assert s.style.font_size == 52 and s.tour_seen and not s.first_run
    assert s.update.auto_check is True and s.update.skipped_version == "" and s.update.updating_from == ""
    assert s.settings_version == Settings.settings_version
    save_settings(s)
    again = load_settings()
    assert again.translate.gemini_key == "anahtar" and again.update.auto_check is True


# --- servis -----------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is not None and (not isinstance(app, QApplication) or app.platformName() != "offscreen"):
        app.shutdown()
        app = None
    if app is None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"  # QApplication'dan hemen önce
        app = QApplication([])
    return app


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


class Lock:
    """winutil'in tek örnek kilidi yerine: çağrılar sırayla."""

    def __init__(self, monkeypatch):
        from altyazitr import winutil

        self.calls: list[str] = []
        monkeypatch.setattr(winutil, "release_single_instance", lambda: self.calls.append("bırak"))
        monkeypatch.setattr(winutil, "reacquire_single_instance", lambda: self.calls.append("geri al"))


def _service(gh, tmp_path, kind="user", settings=None, launcher=None, version="1.3.0"):
    from altyazitr.update_service import UpdateService

    s = settings or Settings()
    saved: list[int] = []
    svc = UpdateService(s, lambda: saved.append(1), source=gh.source, kind=kind, version=version,
                        download_dir=tmp_path / "guncelleme", launcher=launcher)
    svc.saved = saved  # type: ignore[attr-defined]
    return svc


def test_service_timer_is_hourly_and_backs_off(qapp, gh, tmp_path):
    from altyazitr import update_service as us

    assert us.FIRST_CHECK_MS == 3_000 and us.CHECK_EVERY_MS == 3_600_000
    _release_json(gh)
    svc = _service(gh, tmp_path)
    assert not svc._timer.isActive()  # oluşturulunca istek yok (AppContext; main() başlatır)
    svc.start()
    assert svc._timer.isActive() and svc._timer.isSingleShot() and svc._timer.interval() == us.FIRST_CHECK_MS
    assert gh.api_requests() == []
    svc._timer.stop()
    svc._on_timer()  # süre doldu
    assert _wait(qapp, lambda: not svc.checking)
    assert svc._timer.isActive() and svc._timer.interval() == us.CHECK_EVERY_MS
    assert svc.release.version == "1.3.1" and svc.status == "available" and svc.s.update.last_check > 0
    gh.api_status, gh.api_headers = 429, {"Retry-After": "7200"}
    svc.check_now(manual=False)
    assert _wait(qapp, lambda: not svc.checking)
    assert svc.status == "limited" and svc._timer.interval() == 7200 * 1000
    assert svc.release.version == "1.3.1"  # oran sınırında bilinen sürüm kalır
    svc.set_auto(False)
    assert not svc._timer.isActive() and svc.s.update.auto_check is False
    svc.set_auto(True)
    assert svc._timer.isActive()
    svc.shutdown()
    assert not svc._timer.isActive()


def test_service_auto_check_off_makes_no_request(qapp, gh, tmp_path):
    s = Settings()
    s.update.auto_check = False
    svc = _service(gh, tmp_path, settings=s)
    svc.start()
    assert not svc._timer.isActive()
    svc.check_now(manual=True)  # Şimdi denetle yine çalışır
    assert _wait(qapp, lambda: not svc.checking)
    assert len(gh.api_requests()) == 1 and not svc._timer.isActive()


def test_found_once_skip_and_manual(qapp, gh, tmp_path):
    _release_json(gh)
    svc = _service(gh, tmp_path)
    found: list[tuple[str, bool]] = []
    svc.found.connect(lambda rel, manual: found.append((rel.version, manual)))
    for _ in range(2):  # ikinci denetim 304: yeniden bildirilmez
        svc.check_now(manual=False)
        assert _wait(qapp, lambda: not svc.checking)
    assert found == [("1.3.1", False)] and len(gh.api_requests()) == 2
    svc.skip()
    assert svc.s.update.skipped_version == "1.3.1" and svc.available is None and svc.saved
    svc2 = _service(gh, tmp_path, settings=svc.s)  # sonraki açılış: atlanan sürüm bildirilmez
    found2: list = []
    svc2.found.connect(lambda rel, manual: found2.append((rel.version, manual)))
    svc2.check_now(manual=False)
    assert _wait(qapp, lambda: not svc2.checking)
    assert found2 == [] and svc2.release.version == "1.3.1"
    svc2.check_now(manual=True)  # kullanıcı açıkça sordu: yine gösterilir
    assert _wait(qapp, lambda: not svc2.checking)
    assert found2 == [("1.3.1", True)] and svc2.s.update.skipped_version == ""
    _release_json(gh, "1.3.2")
    svc.check_now(manual=False)  # daha yeni sürüm: atlanmış olsa da bildirilir
    assert _wait(qapp, lambda: not svc.checking)
    assert found[-1] == ("1.3.2", False)


def test_same_or_older_version_is_not_offered(qapp, gh, tmp_path):
    _release_json(gh, "1.3.0")
    svc = _service(gh, tmp_path, version="1.3.0")
    svc.check_now(manual=True)
    assert _wait(qapp, lambda: not svc.checking)
    assert svc.release is None and svc.status == "current"


@pytest.mark.parametrize("kind", ["portable", "all_users", "source"])
def test_service_never_installs_when_not_self_updatable(qapp, gh, tmp_path, monkeypatch, kind):
    _release_json(gh)
    launched: list = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launched.append(a))
    svc = _service(gh, tmp_path, kind=kind, launcher=lambda *a: launched.append(a))
    svc.check_now()
    assert _wait(qapp, lambda: not svc.checking)
    assert svc.available is not None and not svc.can_self_update
    before = len(gh.requests)
    assert svc.download() is False and svc.phase == "idle"
    assert svc.install() is False
    assert launched == [] and len(gh.requests) == before  # dosya indirilmez, süreç başlatılmaz


def test_service_downloads_backs_up_and_launches(qapp, gh, tmp_path, monkeypatch):
    _release_json(gh)
    lock = Lock(monkeypatch)
    config.SETTINGS_FILE.parent.mkdir(parents=True)
    s = Settings()
    s.translate.engine = "ollama"
    save_settings(s)
    launched: list[tuple[Path, int]] = []
    svc = _service(gh, tmp_path, settings=s, launcher=lambda path, pid: launched.append((Path(path), pid)))
    svc._save_now = lambda: save_settings(s)
    svc.check_now()
    assert _wait(qapp, lambda: not svc.checking)
    assert svc.download() is True and svc.phase == "downloading"
    assert _wait(qapp, lambda: svc.phase != "downloading")
    assert svc.phase == "ready" and svc.ready_path.read_bytes() == SETUP and svc.fraction == 1.0
    assert svc.install() is True
    assert launched == [(svc.ready_path, os.getpid())] and svc.launched and svc.phase == "installing"
    assert lock.calls == ["bırak"]
    backup = config.SETTINGS_FILE.with_name("ayarlar.yedek-1.3.0.json")
    assert json.loads(backup.read_text(encoding="utf-8"))["translate"]["engine"] == "ollama"
    assert json.loads(backup.read_text(encoding="utf-8"))["update"]["updating_from"] == ""
    assert load_settings().update.updating_from == "1.3.0"  # yeni sürüm açılınca sonucu bildirir


def test_service_launch_failure_keeps_app_running(qapp, gh, tmp_path, monkeypatch):
    _release_json(gh)
    lock = Lock(monkeypatch)

    def fail(path, pid):
        raise UpdateError("Güncelleme başlatılamadı: antivirüs programı ya da şirket ilkesi engelledi.", "blocked")

    svc = _service(gh, tmp_path, launcher=fail)
    svc.check_now()
    assert _wait(qapp, lambda: not svc.checking)
    svc.download()
    assert _wait(qapp, lambda: svc.phase != "downloading")
    assert svc.install() is False
    assert svc.phase == "failed" and svc.failure.kind == "blocked" and not svc.launched
    assert lock.calls == ["bırak", "geri al"] and svc.s.update.updating_from == ""


def test_service_tampered_file_is_not_launched(qapp, gh, tmp_path):
    _release_json(gh)
    launched: list = []
    svc = _service(gh, tmp_path, launcher=lambda *a: launched.append(a))
    svc.check_now()
    assert _wait(qapp, lambda: not svc.checking)
    svc.download()
    assert _wait(qapp, lambda: svc.phase != "downloading")
    svc.ready_path.write_bytes(b"degisti" + SETUP[7:])  # indirmeden sonra değiştirildi
    assert svc.install() is False and launched == [] and svc.failure.kind == "digest"
    assert not svc.ready_path or not Path(svc.ready_path).exists()


def test_service_cancel_download(qapp, gh, tmp_path):
    _release_json(gh)
    svc = _service(gh, tmp_path)
    svc.check_now()
    assert _wait(qapp, lambda: not svc.checking)
    svc._cancel.set()  # indirme ilk parçada durur
    svc.download()
    svc.cancel()
    assert _wait(qapp, lambda: svc.phase != "downloading")
    assert svc.phase in ("idle", "ready")  # çok hızlı biterse hazır olabilir; iptalde hata yok
    assert svc.failure is None


def test_post_update_note(qapp, gh, tmp_path):
    s = Settings()
    s.update.updating_from = "1.2.1"
    svc = _service(gh, tmp_path, settings=s, version="1.3.0")
    assert svc.post_note == ("Robz Vision 1.3.0 sürümüne güncellendi", "info")
    assert s.update.updating_from == "" and svc.saved
    s.update.updating_from = "1.3.0"
    text, level = _service(gh, tmp_path, settings=s, version="1.3.0").post_note
    assert level == "warn" and text.startswith("Güncelleme kurulamadı") and "Ayrıntı:" in text
    assert _service(gh, tmp_path).post_note is None


def test_cleanup_removes_old_downloads(tmp_path):
    root = tmp_path / "guncelleme"
    (root / "1.3.1").mkdir(parents=True)
    (root / "1.3.1" / setup_name("1.3.1")).write_bytes(b"x")
    updater.cleanup_downloads(root)
    assert list(root.iterdir()) == []
    updater.cleanup_downloads(tmp_path / "yok")  # klasör yoksa sessiz


def test_check_result_error_text_has_no_crash():
    assert CheckResult("error", error="x").release is None


# --- release.yml ve kurulum betiği: güncelleyicinin dayandığı şeyler ---------------------------------------------------
def test_release_workflow_still_produces_what_the_updater_expects():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'tags: ["v*"]' in wf and '"v$ver"' in wf  # etiket vX.Y.Z ve uygulama sürümüyle aynı
    assert "installer\\RobzVision.iss" in wf and "/DAppVersion=" in wf
    assert "SHA256SUMS.txt" in wf and "Get-FileHash" in wf and ".Hash.ToLower()" in wf
    assert '"{0}  {1}"' in wf  # "<özet>  <dosya>" (iki boşluk): updater.parse_sums
    assert "files: cikti/*" in wf and "make_latest: true" in wf
    assert "prerelease: true" not in wf and "draft: true" not in wf  # son sürüm olarak yayımlanır
    iss = (ROOT / "installer" / "RobzVision.iss").read_text(encoding="utf-8")
    m = re.search(r"^OutputBaseFilename=(.+)$", iss, re.M)
    assert m and m[1].strip() + ".exe" == updater.SETUP_PATTERN.replace("{version}", "{#AppVersion}")
    assert updater.SUMS_NAME == "SHA256SUMS.txt"
    notes = (ROOT / "tools" / "surum_notlari.py").read_text(encoding="utf-8")
    assert "## Bu sürümde" in notes  # sürüm notlarının gösterilen bölümü (updater.release_notes)


def _sections(iss: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = ""
    for line in iss.splitlines():
        m = re.fullmatch(r"\[(\w+)\]\s*", line.strip())
        if m:
            cur = m[1]
            out.setdefault(cur, [])
        elif cur and line.strip() and not line.lstrip().startswith((";", "//")):
            out[cur].append(line.strip())
    return out


def test_installer_supports_silent_update_and_keeps_user_data():
    """Kurulum betiği: AppId ve AppMutex uygulamayla aynı; /GUNCELLEME ve /BEKLE işlenir; güncelleme yalnız kurulum
    klasörünü değiştirir, ayarlara ve modellere dokunmaz; kısayol tercihi korunur."""
    iss = (ROOT / "installer" / "RobzVision.iss").read_text(encoding="utf-8")
    sec = _sections(iss)
    setup = "\n".join(sec["Setup"])
    assert f"AppId={{{updater.INNO_APP_ID}" in setup  # Inno'da "{{" bir "{" yazar
    assert "AppMutex=RobzVision_TekOrnek" in setup
    app_py = (ROOT / "altyazitr" / "app.py").read_text(encoding="utf-8")
    assert 'acquire_single_instance("Local\\\\RobzVision_TekOrnek")' in app_py
    assert "PrivilegesRequired=lowest" in setup and "RestartApplications=no" in setup
    # silinenler: kurulumda yalnız {app} içi; kaldırmada {app} içi ve yalnız indirme klasörleri
    for line in sec.get("InstallDelete", []):
        name = re.search(r'Name:\s*"([^"]+)"', line)[1]
        assert name.startswith("{app}\\") and len(name) > len("{app}\\"), line
    allowed = {"{localappdata}\\RobzVision\\indirilenler", "{localappdata}\\RobzVision\\guncelleme"}
    for line in sec.get("UninstallDelete", []):
        name = re.search(r'Name:\s*"([^"]+)"', line)[1]
        assert name.startswith("{app}\\") or name in allowed, line
    code = "\n".join(sec["Code"])
    for bad in ("DelTree", "DeleteFile", "RemoveDir", "userappdata", "RenameFile"):
        assert bad not in code, bad  # [Code] kullanıcı verisini silmez
    assert "'/GUNCELLEME'" in code and "{param:BEKLE|0}" in code and "WaitForSingleObject" in code
    run = sec["Run"]
    update_run = [r for r in run if "Check: GuncellemeKipi" in r]
    assert len(update_run) == 1 and "skipifsilent" not in update_run[0] and "postinstall" not in update_run[0]
    assert "{app}\\{#AppExe}" in update_run[0]
    assert any("postinstall" in r and "not GuncellemeKipi" in r for r in run)  # elle kurulumda eskisi gibi
    desk = [i for i in sec["Icons"] if "{autodesktop}" in i]
    assert len(desk) == 1 and "Tasks: desktopicon" in desk[0] and "Check: MasaustuKisayoluKurulsun" in desk[0]
    assert "FileExists(ExpandConstant('{autodesktop}\\{#AppName}.lnk'))" in code
    assert "UsePreviousTasks=no" not in setup  # önceki kısayol seçimi hatırlanır
    for flag in ("/GUNCELLEME", "/SUPPRESSMSGBOXES", "/NORESTART"):
        assert flag in updater.SETUP_FLAGS
    assert "BEKLE" in " ".join(updater.setup_command(Path("x.exe"), 1))
