"""Yapay zekâ kurulum servisi (altyazitr.ai_setup): plan, adımların sırası, duraklatma ve sürdürme, iptal, hata
türleri ("Tekrar dene" / "Elle kur"), kalıcı durum (settings.ai), hız testi kararı ve kapanış.

Ağ yok, gerçek modüller (hardware, ollama_install, ollama_runtime) hiç yüklenmez: servis sahte `deps` ile çalışır;
sahteler gerçek modüllerin davranışını taklit eder (hata metni + kind, kurulumun dosyayı silmesi, model çekmede
büyüyen toplam). İş gerçek QThread'de yürür; sahte işlevler threading.Event "kapıları"nda test izin verene ya da
isteği kesilene kadar bekler. Hiçbir test süre ölçmez: olay döngüsü beklenen durum gelene kadar döndürülür (bkz.
test_translate_page.py). İlerleme saati sahtedir (her okumada 1 sn ilerler): her bildirim geçer, sonuç makinenin
hızına bağlı değildir. Olay döngüsü için QCoreApplication yeter; pencere açılmaz (bkz. qapp)."""

from __future__ import annotations

import gc
import itertools
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from altyazitr import ai_setup as ai
from altyazitr.ai_setup import AiSetup, AiSetupError, Plan, Step

GB, GIB, MIB = ai.GB, ai.GIB, 1 << 20
D, I, S, C, P, T = Step.DOWNLOAD, Step.INSTALL, Step.START, Step.CHECK, Step.PULL, Step.TEST
SETUP, MARGIN, OLLAMA_DISK = ai.SETUP_BYTES, ai.DISK_MARGIN, ai.OLLAMA_DISK_BYTES
M12 = ai.MODEL_INFO["gemma4:12b"].size
NEW = (0, 40, 16)

INST_NEW = SimpleNamespace(app_exe=Path("C:/Ollama/ollama app.exe"), version="0.40.16", uninstall="")
INST_OLD = SimpleNamespace(app_exe=Path("C:/Ollama/ollama app.exe"), version="0.12.3", uninstall="")
# Ollama günlüğünden okunan "inference compute" satırları (12 GB kart ~11,7-12,0 GiB raporlar)
GPU_12 = SimpleNamespace(library="CUDA", name="NVIDIA GeForce RTX 4070", kind="discrete", total=12 * GIB - 300 * MIB,
                         available=11 * GIB)
GPU_8 = SimpleNamespace(library="CUDA", name="NVIDIA GeForce RTX 4060", kind="discrete", total=8 * GIB - 200 * MIB,
                        available=7 * GIB)
IGPU = SimpleNamespace(library="Vulkan", name="AMD Radeon 780M", kind="iGPU", total=8 * GIB, available=6 * GIB)
CPU = SimpleNamespace(library="cpu", name="cpu", kind="", total=32 * GIB, available=20 * GIB)


@pytest.fixture(scope="module")
def qapp():
    """Servis yalnız QtCore kullanır: olay döngüsü için QCoreApplication yeter (pencere yok, 'offscreen' gerekmez).
    Bu modül test_core'dan önce çalışır: kendi kurduğu uygulamayı sonunda kapatır ki test_core kendi
    QGuiApplication'ını (gerçek ekran eklentisiyle) kurabilsin. Önceden kurulmuş bir uygulama varsa onu kullanır."""
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    created = app is None
    if created:
        app = QCoreApplication([])
    yield app
    if created:
        app.shutdown()


def _wait(qapp, cond, timeout: float = 5.0) -> bool:
    """Arka plan işinin olayları arayüz iş parçacığında işlensin diye olay döngüsünü döndürür."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    qapp.processEvents()
    return cond()


def mod_error(text: str, kind: str, module: str = "ollama_install") -> RuntimeError:
    """Modüllerin hatası gibi (ollama_install.SetupError, ollama_runtime.PullError): Türkçe metin + tür."""
    cls = type("SetupError" if module == "ollama_install" else "PullError", (RuntimeError,),
               {"__module__": f"altyazitr.{module}"})
    err = cls(text)
    err.kind = kind
    return err


class FakeDeps:
    """Sahte dış işlevler: Ollama, kurulum dosyası ve ekran kartı bellekte. Çağrılar `calls`ta sırayla durur.
    hold(ad) o işlevi release(ad) çağrılana kadar bekletir; indirmeler beklerken isteği kesilirse (iptal işareti)
    "İptal edildi." (kind="cancel") fırlatır; doğrulama, kurulum ve Ollama'nın açılmasını bekleme kesilemez.
    errors[ad]: o işlevin sırayla fırlatacağı hatalar. Kurulum dosyası gerçeği gibi önce .part'a iner (iptalde ve
    duraklatmada kalır), discard_download ikisini de siler; marked: "Ollama'yı biz kurduk" işareti yazıldı mı."""

    def __init__(self, tmp_path: Path, *, api: bool = False, version=None, installed=None, models=(),
                 other_on_port: bool = False, computes=(GPU_12,), free: int = 500 * GB):
        self.api, self.version, self.installed = api, version, installed
        self.models = list(models)
        self.other_on_port = other_on_port
        self.computes = list(computes)
        self.free = free
        self.setup_file = tmp_path / "OllamaSetup.exe"
        self.part_file = tmp_path / "OllamaSetup.exe.part"
        self.marked = False
        self.pull_reports: list[tuple[int, int]] | None = None  # (yapılan, toplam) sırası; None: varsayılan
        self.speed = None  # speed_test'in döndüreceği sonuç
        self.speed_args: tuple | None = None
        self.calls: list[str] = []
        self.pulls: list[str] = []
        self.timeouts: list[float] = []
        self.waiting: set[str] = set()  # şu an kapıda bekleyen çağrılar
        self.returned: list[str] = []  # sonuna kadar çalışıp dönen çağrılar
        self.cancelled: list[str] = []  # isteği kesilen çağrılar
        self.errors: dict[str, list[BaseException]] = {}
        self._gates: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # --- testin denetimi ---------------------------------------------------------------------------------------
    def hold(self, name: str) -> None:
        self._gates[name] = threading.Event()

    def release(self, name: str) -> None:
        self._gates[name].set()

    def release_all(self) -> None:
        for gate in list(self._gates.values()):
            gate.set()

    def count(self, name: str) -> int:
        return self.calls.count(name)

    def work(self) -> list[str]:
        """Yoklamalar dışındaki çağrılar, sırayla."""
        return [c for c in self.calls if c not in ("probe", "find_installed")]

    def _enter(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
            errors = self.errors.get(name)
            error = errors.pop(0) if errors else None
        if error is not None:
            raise error

    def _gate(self, name: str, cancel: threading.Event | None = None) -> None:
        gate = self._gates.get(name)
        if gate is None:
            return
        with self._lock:
            self.waiting.add(name)
        try:
            while not gate.wait(0.002):
                if cancel is not None and cancel.is_set():
                    with self._lock:
                        self.cancelled.append(name)
                    raise mod_error("İptal edildi.", "cancel")
        finally:
            with self._lock:
                self.waiting.discard(name)

    def _return(self, name: str) -> None:
        with self._lock:
            self.returned.append(name)

    # --- ollama_install ----------------------------------------------------------------------------------------
    def download_setup(self, progress, cancel):
        self._enter("download_setup")
        progress(0, SETUP)
        self.part_file.write_bytes(b"MZ")  # yarım indirme (iptalde ve duraklatmada kalır)
        progress(SETUP // 2, SETUP)
        self._gate("download_setup", cancel)
        progress(SETUP, SETUP)
        self.part_file.replace(self.setup_file)
        self._return("download_setup")
        return self.setup_file

    def discard_download(self):
        self._enter("discard_download")
        self.setup_file.unlink(missing_ok=True)
        self.part_file.unlink(missing_ok=True)
        self._return("discard_download")

    def verify_setup(self, path):
        self._enter("verify_setup")
        self._gate("verify_setup")  # doğrulamanın iptal işareti yok

    def run_setup(self, path, log_file):
        self._enter("run_setup")
        self._gate("run_setup")  # kurulum sürecinin iptal işareti yok
        self.installed = INST_NEW
        if self.api:  # Inno açık Ollama'yı kapatıp yenisini başlatır
            self.version = NEW
        Path(path).unlink(missing_ok=True)  # gerçeği gibi: başarıda kurulum dosyasını siler
        self._return("run_setup")

    def find_installed(self):
        self._enter("find_installed")
        return self.installed

    def mark_installed_by_us(self):
        self._enter("mark_installed_by_us")
        self.marked = True

    # --- ollama_runtime ----------------------------------------------------------------------------------------
    def probe(self, url):
        self._enter("probe")
        return SimpleNamespace(url=url, api=self.api, version=self.version if self.api else None,
                               other_on_port=self.other_on_port, models=list(self.models) if self.api else [],
                               installed=self.installed)

    def ensure_running(self, url, timeout=25.0):
        self._enter("ensure_running")
        self.timeouts.append(timeout)
        self._gate("ensure_running")  # gerçeği gibi kesilemez: Ollama'nın açılmasını en çok timeout sn bekler
        if self.installed is not None and not self.other_on_port:
            self.api, self.version = True, ai.parse_version(self.installed.version)
        self._return("ensure_running")
        return self.api

    def inference_compute(self, log_file):
        self._enter("inference_compute")
        return list(self.computes)

    def pull(self, url, model, progress, cancel):
        self._enter("pull")
        self.pulls.append(model)
        size = ai.model_size(model) or GB
        for done, total in self.pull_reports or [(0, 0), (size // 4, size)]:
            progress(done, total, "İndiriliyor")
        self._gate("pull", cancel)
        if self.pull_reports is None:
            progress(size, size, "Tamamlandı")
        self.models.append(model)
        self._return("pull")

    def speed_test(self, url, model, profile_id, lines=None):
        self._enter("speed_test")
        self.speed_args = (url, model, profile_id)
        return self.speed

    # --- hardware ----------------------------------------------------------------------------------------------
    def free_space(self, path):
        return self.free

    def list_gpus(self):
        return []

    def advise(self, gpus, *, ram, win_build):
        return SimpleNamespace(tier="good", offer=True, preselect=True, model="gemma4:12b")

    def windows_build(self):
        return 26200


def _settings(**ai_fields) -> SimpleNamespace:
    """config.py'deki Settings'in servisin kullandığı kısmı (settings.ai: config.AiCfg ile aynı alanlar)."""
    fields = dict(choice="", pending_model="", failed="", failed_kind="", pause_while_running=True,
                  installed_ollama=False, invite_pending=True, speed_ms=0)
    fields.update(ai_fields)
    return SimpleNamespace(ai=SimpleNamespace(**fields),
                           translate=SimpleNamespace(engine="quickmt", ollama_model="translategemma:12b",
                                                     ollama_url="http://127.0.0.1:11434", profile="gta6"))


@pytest.fixture
def make(qapp):
    """Servis + gözlemciler: her kayıtta ayarların özeti (saves), finished sinyalleri, adımların ve durumların sırası.
    Test bitince servis kapatılır, kapılar açılır ve son dış çağrının bitmesi beklenir."""
    made: list[SimpleNamespace] = []

    def factory(deps: FakeDeps, s: SimpleNamespace | None = None, **ai_fields) -> SimpleNamespace:
        s = s if s is not None else _settings(**ai_fields)
        h = SimpleNamespace(s=s, deps=deps, saves=[], finished=[], steps=[], states=[])

        def save():
            h.saves.append((s.translate.engine, s.translate.ollama_model, s.ai.pending_model, s.ai.failed))

        svc = AiSetup(s, save, deps, retry_delays=(0.0, 0.0, 0.0), clock=itertools.count(0.0, 1.0).__next__)

        def on_changed():
            if svc.step is not None and (not h.steps or h.steps[-1] is not svc.step):
                h.steps.append(svc.step)
            if not h.states or h.states[-1] != svc.state:
                h.states.append(svc.state)

        svc.changed.connect(on_changed)
        svc.finished.connect(lambda ok, text: h.finished.append((ok, text)))
        h.svc = svc
        made.append(h)
        return h

    yield factory
    for h in made:
        assert h.svc.shutdown(5000)
        h.deps.release_all()
        assert h.svc._helper_done.wait(5), "sahte dış çağrı bitmedi"
    made.clear()
    gc.collect()  # servisler (ve bitmiş iş parçacıkları) uygulama kapanmadan silinsin


# --- plan ----------------------------------------------------------------------------------------------------
MODELS_DIR = Path("C:/Users/robz/.ollama/models")
APP_DIR = Path("C:/Users/robz/AppData/Local")


def _probe(*, api=False, version=None, installed=None, models=(), other_on_port=False):
    return SimpleNamespace(url=ai.DEFAULT_URL, api=api, version=version, other_on_port=other_on_port,
                           models=list(models), installed=installed)


def _build(probe, model="gemma4:12b", apply=True, *, free=500 * GB, win_build=26200, update=False) -> Plan:
    return Plan.build(probe, model, apply, free_space=lambda p: free, win_build=win_build, update=update,
                      models_dir=MODELS_DIR, app_dir=APP_DIR)


@pytest.mark.parametrize(("probe", "apply", "steps", "download", "disk", "version"), [
    pytest.param(_probe(), True, [D, I, S, C, P], SETUP + M12, M12 + SETUP + OLLAMA_DISK + MARGIN, "",
                 id="ollama yok"),
    pytest.param(_probe(api=True, version=NEW, installed=INST_NEW, models=["llama3.2:3b"]), True, [C, P], M12,
                 M12 + MARGIN, "0.40.16", id="ollama açık, model yok"),
    pytest.param(_probe(api=True, version=NEW, installed=INST_NEW, models=["gemma4:12b"]), True, [], 0, 0,
                 "0.40.16", id="hepsi kurulu: yalnız ayar"),
    pytest.param(_probe(installed=INST_NEW), True, [S, C, P], M12, M12 + MARGIN, "0.40.16",
                 id="ollama kurulu ama kapalı"),
    pytest.param(_probe(api=True, version=(0, 12, 3), installed=INST_OLD), True, [D, I, S, C, P], SETUP + M12,
                 M12 + SETUP + MARGIN, "0.12.3", id="eski ollama yerinde güncellenir"),
    pytest.param(_probe(api=True, version=NEW, installed=INST_NEW), False, [P], M12, M12 + MARGIN, "0.40.16",
                 id="yalnız model indir (apply=False): GPU kapısı yok"),
])
def test_plan_steps_and_sizes(probe, apply, steps, download, disk, version):
    plan = _build(probe, apply=apply)
    assert plan.steps == steps
    assert (plan.download_bytes, plan.disk_bytes) == (download, disk)
    assert plan.ollama_version == version and plan.updating == (version == "0.12.3")
    assert plan.blocker == "" and plan.apply is apply and plan.model == "gemma4:12b" and plan.url == ai.DEFAULT_URL
    assert plan.disk_path == MODELS_DIR and plan.disk_free == 500 * GB  # aynı sürücü: model klasörü gösterilir


@pytest.mark.parametrize(("probe", "kw", "blocker", "kind"), [
    pytest.param(_probe(other_on_port=True), {}, "11434 portunu başka bir program kullanıyor.", "port",
                 id="port dolu"),
    pytest.param(_probe(), {"win_build": 19044}, ai.TEXT_WINDOWS_OLD, "windows", id="windows eski"),
    pytest.param(_probe(), {"win_build": 0}, "", "", id="windows sürümü okunamadı: engel değil"),
    pytest.param(_probe(), {"free": 9 * GB}, "C: sürücüsünde yer yetmiyor: 9 GB boş, ~15 GB gerekli", "disk",
                 id="disk yetmez"),
    pytest.param(_probe(api=True, version=(0, 12, 3)), {},
                 "Ollama'nın sürümü eski (0.12.3). Ollama'yı güncelleyip yeniden deneyin.", "outdated",
                 id="eski ve güncellenemez (Docker/WSL/zip)"),
])
def test_plan_blockers(probe, kw, blocker, kind):
    plan = _build(probe, **kw)
    assert (plan.blocker, plan.blocker_kind) == (blocker, kind)


def test_plan_checks_each_drive():
    """Model klasörü başka sürücüdeyse (OLLAMA_MODELS) iki sürücü ayrı denetlenir; gösterilen en çok yer gerekeni."""
    free = {"C:": 200 * GB, "D:": 3 * GB}
    plan = Plan.build(_probe(), "gemma4:12b", free_space=lambda p: free[p.drive], win_build=26200,
                      models_dir=Path("D:/modeller"), app_dir=APP_DIR)
    assert plan.disk_path == Path("D:/modeller") and plan.disk_bytes == M12 + MARGIN and plan.disk_free == 3 * GB
    assert plan.blocker == "D: sürücüsünde yer yetmiyor: 3 GB boş, ~9 GB gerekli"


def test_plan_forced_update():
    """update=True ("sürüm eski" hatasından sonra): resmî kurulum uygulamanın sürümünden eskiyse güncellenir; elle
    kurulmuş (Docker/zip) ya da zaten yeni Ollama'ya dokunulmaz."""
    inst = SimpleNamespace(app_exe=INST_NEW.app_exe, version="0.35.0", uninstall="")
    plan = _build(_probe(api=True, version=(0, 35, 0), installed=inst), update=True)
    assert plan.steps == [D, I, S, C, P] and plan.updating
    assert _build(_probe(api=True, version=(0, 35, 0)), update=True).steps == [C, P]
    assert _build(_probe(api=True, version=NEW, installed=INST_NEW), update=True).steps == [C, P]


def test_speed_test_step_is_off_by_default():
    assert ai.SPEED_TEST is False and T not in _build(_probe()).steps
    plan = Plan.build(_probe(api=True, version=NEW, models=["gemma4:12b"]), "gemma4:12b", test=True,
                      win_build=26200)
    assert plan.steps == [T]


# --- adımlar ---------------------------------------------------------------------------------------------------
def test_fresh_install_runs_steps_in_order(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path)
    h = make(deps)
    svc = h.svc
    plan = svc.plan()  # model verilmedi: donanım önerisindeki
    assert plan.model == "gemma4:12b" and plan.steps == [D, I, S, C, P]
    svc.start(plan)
    assert svc.state == "running" and h.s.ai.pending_model == "gemma4:12b"
    assert _wait(qapp, lambda: svc.state == "done")
    assert h.steps == [D, I, S, C, P]
    # "Ollama'yı biz kurduk" işareti kurulumdan önce (bkz. test_installed_by_us_mark_survives_interrupted_install)
    assert deps.work() == ["download_setup", "verify_setup", "mark_installed_by_us", "run_setup", "ensure_running",
                           "inference_compute", "pull"]
    assert deps.timeouts == [60.0]  # yeni kurulan Ollama'nın açılması daha uzun beklenir
    assert h.s.ai.installed_ollama and h.s.ai.pending_model == "" and h.s.ai.failed == ""
    assert svc.fraction == 1.0 and svc.eta_s == 0 and svc.step is None
    assert h.finished == [(True, ai.TEXT_READY)] and svc.message == ai.TEXT_READY


@pytest.mark.parametrize(("profile", "expected"), [("", "genel"), ("genel", "genel")],
                         ids=["boş profil genel olur", "profil korunur"])
def test_success_writes_engine_and_model_then_saves(make, qapp, tmp_path, profile, expected):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    h = make(deps)
    h.s.translate.profile = profile
    h.svc.start(h.svc.plan("gemma4:12b"))
    assert _wait(qapp, lambda: h.svc.state == "done")
    t = h.s.translate
    assert (t.engine, t.ollama_model, t.profile, t.ollama_url) == ("ollama", "gemma4:12b", expected, ai.DEFAULT_URL)
    assert deps.work() == ["inference_compute", "pull"] and deps.pulls == ["gemma4:12b"]
    # başlarken pending_model yazılıp kaydedildi; son kayıt ayarlar uygulandıktan sonra
    assert h.saves[0] == ("quickmt", "translategemma:12b", "gemma4:12b", "")
    assert h.saves[-1] == ("ollama", "gemma4:12b", "", "")
    assert not h.s.ai.invite_pending and not h.s.ai.installed_ollama  # Ollama'yı biz kurmadık
    assert h.finished == [(True, ai.TEXT_READY)]


def test_no_restart_while_pipeline_runs(make, qapp, tmp_path):
    """Çeviri çalışırken biten kurulum ayarı yazar ama hattı yeniden başlatmaz: yeni motor bir sonraki Başlat'ta
    devreye girer (servisin hatta erişimi yok; ileti bunu söyler). Duraklatma kapalıysa indirme kesilmez."""
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.hold("pull")
    h = make(deps, pause_while_running=False)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "pull" in deps.waiting)
    h.svc.on_pipeline_running(True)
    deps.release("pull")
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.cancelled == [] and deps.count("pull") == 1 and "paused" not in h.states
    assert (h.s.translate.engine, h.s.translate.ollama_model) == ("ollama", "gemma4:12b")
    assert h.saves[-1][:2] == ("ollama", "gemma4:12b")
    assert h.finished == [(True, ai.TEXT_READY_LATER)] and h.svc.message == ai.TEXT_READY_LATER


def test_model_only_download_keeps_settings(make, qapp, tmp_path):
    """Çeviri sayfasındaki "Modeli indir" (apply=False): yalnız model iner, ayar ve kalıcı durum değişmez."""
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    h = make(deps)
    plan = h.svc.plan("qwen3:8b", apply=False)
    assert plan.steps == [P] and plan.download_bytes == 0  # boyutu bilinmeyen model
    h.svc.start(plan)
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.work() == ["pull"] and deps.pulls == ["qwen3:8b"]
    assert (h.s.translate.engine, h.s.translate.ollama_model) == ("quickmt", "translategemma:12b")
    assert h.s.ai.pending_model == "" and all(save[2] == "" for save in h.saves)
    assert h.finished == [(True, "qwen3:8b indirildi.")]


def test_steps_recheck_their_preconditions(make, qapp, tmp_path):
    """Her adım başlarken ön koşuluna yeniden bakar: iş duraklamışken Ollama başka yoldan kurulduysa indirme ve
    kurulum atlanır (sürdürme ve "Tekrar dene" aynı yoldan geçer)."""
    deps = FakeDeps(tmp_path)
    h = make(deps)
    h.svc.on_pipeline_running(True)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "paused")
    assert h.svc.step is D and deps.work() == []  # duraklamadayken hiçbir adım başlamaz
    deps.installed, deps.api, deps.version = INST_NEW, True, NEW  # kullanıcı Ollama'yı bu arada kendisi kurdu
    h.svc.on_pipeline_running(False)
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.work() == ["inference_compute", "pull"]
    assert not h.s.ai.installed_ollama and h.svc.fraction == 1.0


def test_model_download_progress_never_goes_back(make, qapp, tmp_path):
    """Ollama'nın bildirdiği toplam katmanlar göründükçe büyür (önce ~0,1 GB, sonra 7,4 GB): adımın boyutu
    max(bildirilen, MODEL_INFO) alınır, çubuk ve kalan süre geri düşmez."""
    small, big = 100 * 10**6, int(7.4 * GB)
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.pull_reports = [(0, small), (small, small), (small + GB, small + big), (small + big, small + big)]
    h = make(deps)
    seen: list[tuple] = []
    h.svc.changed.connect(lambda: seen.append((h.svc.step, h.svc.total_bytes, h.svc.fraction)))
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "done")
    pulling = [(total, frac) for step, total, frac in seen if step is P]
    assert len(pulling) >= len(deps.pull_reports) and all(total == M12 for total, _ in pulling)
    fractions = [frac for _, frac in pulling] + [h.svc.fraction]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


# --- duraklatma ve sürdürme ------------------------------------------------------------------------------------
def test_pipeline_running_pauses_download_and_stop_resumes(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path)
    deps.hold("download_setup")
    h = make(deps)
    svc = h.svc
    svc.start(svc.plan())
    assert _wait(qapp, lambda: "download_setup" in deps.waiting and svc.done_bytes == SETUP // 2)
    assert (svc.total_bytes, svc.all_done_bytes, svc.all_total_bytes) == (SETUP, SETUP // 2, SETUP + M12)
    assert svc.eta_s is not None and 0.0 < svc.fraction < 0.1  # Ekran 3: "%9  Kalan ~… dk"
    svc.on_pipeline_running(True)
    assert _wait(qapp, lambda: svc.state == "paused")
    assert deps.cancelled == ["download_setup"]  # süren istek kesildi
    assert svc.step is D and svc.message == ai.TEXT_PAUSED and svc.eta_s is None
    qapp.processEvents()
    assert deps.count("download_setup") == 1  # duraklarken yeniden başlamaz
    assert deps.part_file.exists() and "discard_download" not in deps.calls  # duraklatma yarım dosyayı silmez
    svc.on_pipeline_running(False)
    assert _wait(qapp, lambda: deps.count("download_setup") == 2 and "download_setup" in deps.waiting)
    assert _wait(qapp, lambda: svc.state == "running")
    deps.release("download_setup")
    assert _wait(qapp, lambda: svc.state == "done")
    assert h.states == ["running", "paused", "running", "done"]
    assert h.finished == [(True, ai.TEXT_READY)] and "discard_download" not in deps.calls


def test_force_resume_ignores_pause(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    h = make(deps)
    h.svc.on_pipeline_running(True)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "paused")
    assert deps.work() == []
    h.svc.force_resume()  # "Yine de sürdür"
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.work() == ["inference_compute", "pull"] and deps.cancelled == []
    assert h.finished == [(True, ai.TEXT_READY_LATER)]  # çeviri hâlâ çalışıyor


def test_install_step_is_not_paused(make, qapp, tmp_path):
    """"Ollama kuruluyor" adımında duraklatma ve iptal kapalıdır; iş o adım bitince, sıradakinden önce durur."""
    deps = FakeDeps(tmp_path)
    deps.hold("run_setup")
    h = make(deps)
    svc = h.svc
    svc.start(svc.plan())
    assert _wait(qapp, lambda: "run_setup" in deps.waiting and not svc.can_cancel)
    assert _wait(qapp, lambda: svc.substep == "install")
    assert svc.step is I and svc.message == "Ollama kuruluyor (1–2 dk)"
    svc.on_pipeline_running(True)
    qapp.processEvents()
    assert svc.state == "running" and "run_setup" in deps.waiting  # kurulum kesilmedi
    deps.release("run_setup")
    assert _wait(qapp, lambda: svc.state == "paused")
    assert svc.step is S and svc.substep == "" and svc.can_cancel
    assert deps.work() == ["download_setup", "verify_setup", "mark_installed_by_us", "run_setup"]
    svc.on_pipeline_running(False)
    assert _wait(qapp, lambda: svc.state == "done")
    assert h.finished == [(True, ai.TEXT_READY)]


# --- Ollama kurulumu: alt adımlar ve "biz kurduk" işareti -------------------------------------------------------------
@pytest.mark.parametrize(("deps_kw", "install_text", "marks"), [
    pytest.param({}, "Ollama kuruluyor (1–2 dk)", True, id="yeni kurulum"),
    pytest.param({"api": True, "version": (0, 12, 3), "installed": INST_OLD}, "Ollama güncelleniyor (1–2 dk)", False,
                 id="eski Ollama güncellenir"),
])
def test_install_reports_verify_then_install(make, qapp, tmp_path, deps_kw, install_text, marks):
    """"Ollama kuruluyor" adımı iki alt adımdır: önce imza ve bütünlük doğrulanır, sonra kurulur. Arayüz satırı
    `substep`ten (ya da "İmza…" iletisinden) seçer; ikisinde de changed yayılır. İkisi de iptal edilemez."""
    deps = FakeDeps(tmp_path, **deps_kw)
    deps.hold("verify_setup")
    deps.hold("run_setup")
    h = make(deps)
    svc = h.svc
    seen: list[tuple] = []  # her changed'de (adım, alt adım, ileti)
    svc.changed.connect(lambda: seen.append((svc.step, svc.substep, svc.message)))
    svc.start(svc.plan())
    assert _wait(qapp, lambda: "verify_setup" in deps.waiting and svc.substep == "verify")
    assert (svc.step, svc.message, svc.can_cancel) == (I, ai.TEXT_VERIFYING, False)
    assert svc.message.startswith("İmza")  # ai_setup_view, substep okunmazsa doğrulama satırını bundan seçer
    deps.release("verify_setup")
    assert _wait(qapp, lambda: "run_setup" in deps.waiting and svc.substep == "install")
    assert (svc.step, svc.message, svc.can_cancel) == (I, install_text, False)
    assert deps.marked is marks  # yeni kurulumda işaret kurulumdan önce yazıldı; güncellemede hiç yazılmaz
    deps.release("run_setup")
    assert _wait(qapp, lambda: svc.state == "done")
    assert svc.substep == "" and h.s.ai.installed_ollama is marks
    during = [(sub, msg) for step, sub, msg in seen if step is I]
    assert during[0] == ("verify", ai.TEXT_VERIFYING)  # adım doğrulamayla başlar: önce "kuruluyor" görünmez
    subs = [sub for sub, _ in during]
    assert subs == sorted(subs, key=["verify", "install"].index)  # doğrulama → kurulum, geri dönmez
    assert during[-1] == ("install", install_text)


def test_update_waits_for_translation_to_stop(make, qapp, tmp_path):
    """Var olan Ollama'yı güncellemek onu kapatır: duraklatma kapalı olsa da çeviri çalışırken güncelleme başlamaz.
    Beklerken sıradaki iş doğrulamadır (alt adım "verify"); sürünce satır yine "İmza ve bütünlük doğrulanıyor"."""
    deps = FakeDeps(tmp_path, api=True, version=(0, 12, 3), installed=INST_OLD)
    deps.hold("verify_setup")
    h = make(deps, pause_while_running=False)
    svc = h.svc
    svc.on_pipeline_running(True)
    svc.start(svc.plan())
    assert _wait(qapp, lambda: svc.state == "paused")
    assert (svc.step, svc.substep, svc.message) == (I, "verify", ai.TEXT_PAUSED)
    assert deps.work() == ["download_setup"]  # indirme duraklamadı (ayar kapalı); güncelleme bekliyor
    svc.on_pipeline_running(False)
    assert _wait(qapp, lambda: "verify_setup" in deps.waiting and svc.state == "running")
    assert (svc.step, svc.substep, svc.message) == (I, "verify", ai.TEXT_VERIFYING)
    deps.release("verify_setup")
    assert _wait(qapp, lambda: svc.state == "done")
    assert not deps.marked and h.finished == [(True, ai.TEXT_READY)]  # güncelleme: "biz kurduk" işareti yok


def test_substep_is_kept_on_failure_and_cleared_on_retry(make, qapp, tmp_path):
    """Doğrulama hatasında hatalı alt adım kalır (arayüz doğrulama satırını kırmızı gösterebilir); yeni iş temizler."""
    deps = FakeDeps(tmp_path)
    deps.errors["verify_setup"] = [mod_error("İndirilen Ollama dosyası bozuk çıktı ve silindi. Tekrar dene'ye basın.",
                                             "digest")]
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert (h.svc.step, h.svc.substep, h.svc.error_kind) == (I, "verify", "digest")
    h.svc.retry()
    assert h.svc.substep == ""
    assert _wait(qapp, lambda: h.svc.state == "done")


def test_installed_by_us_mark_survives_interrupted_install(make, qapp, tmp_path):
    """"Ollama'yı biz kurduk" işareti (kaldırıcı için) kurulumdan ÖNCE yazılır: uygulama kurulum sürerken kapanırsa
    kurulum süreci kendi biter, sonraki açılışta Ollama kurulu bulunur ve kurulum adımı hiç çalışmaz. İşaret yine
    yerindedir."""
    deps = FakeDeps(tmp_path)
    deps.hold("run_setup")
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "run_setup" in deps.waiting and h.s.ai.installed_ollama)
    assert deps.marked and deps.work() == ["download_setup", "verify_setup", "mark_installed_by_us", "run_setup"]
    assert h.svc.shutdown(3000)  # uygulama kapandı, kurulum süreci sürüyor
    deps.release("run_setup")
    assert _wait(qapp, lambda: "run_setup" in deps.returned)  # kurulum kendi bitti: Ollama kurulu ama kapalı

    deps2 = FakeDeps(tmp_path, installed=INST_NEW)  # sonraki açılış
    h2 = make(deps2, s=h.s)
    h2.svc.resume_pending()
    assert _wait(qapp, lambda: h2.svc.state == "done")
    assert deps2.work() == ["ensure_running", "inference_compute", "pull"] and not deps2.marked
    assert h.s.ai.installed_ollama and h.s.translate.engine == "ollama"


# --- iptal, hata, kalıcı durum ------------------------------------------------------------------------------------
def test_cancel_clears_pending_model(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path)
    deps.hold("download_setup")
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "download_setup" in deps.waiting)
    assert h.s.ai.pending_model == "gemma4:12b" and deps.part_file.exists()
    h.svc.cancel()
    # hemen silinir ve kaydedilir: uygulama şimdi kapansa da sonraki açılışta sürmez
    assert h.s.ai.pending_model == "" and h.saves[-1][2] == "" and h.svc.message == ai.TEXT_CANCELLING
    assert _wait(qapp, lambda: h.svc.state == "idle")
    assert _wait(qapp, lambda: deps.cancelled == ["download_setup"])
    assert h.finished == [(False, ai.TEXT_CANCELLED)] and h.svc.message == ai.TEXT_CANCELLED
    assert h.s.ai.failed == "" and h.svc.error == ""
    # yarım kurulum dosyası (1,9 GB'a kadar) silinir: kesilen indirme dosyayı bıraktıktan sonra
    assert _wait(qapp, lambda: "discard_download" in deps.returned)
    assert not deps.part_file.exists() and not deps.setup_file.exists()
    h.svc.resume_pending()
    assert h.svc.state == "idle" and deps.count("download_setup") == 1


def test_cancel_during_install_waits_for_install(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path)
    deps.hold("run_setup")
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "run_setup" in deps.waiting)
    h.svc.cancel()
    assert h.s.ai.pending_model == ""
    qapp.processEvents()
    assert h.svc.state == "running" and "run_setup" in deps.waiting  # kurulum yarıda kesilmez
    deps.release("run_setup")
    assert _wait(qapp, lambda: h.svc.state == "idle")
    assert _wait(qapp, lambda: "discard_download" in deps.returned)
    assert deps.work() == ["download_setup", "verify_setup", "mark_installed_by_us", "run_setup", "discard_download"]
    assert h.s.ai.installed_ollama and deps.marked  # kurulum tamamlandı ve işaretlendi; sonraki adım başlamadı
    assert h.finished == [(False, ai.TEXT_CANCELLED)]


def test_cancel_discards_setup_file_after_running_call(make, qapp, tmp_path):
    """İptal kurulum dosyasını siler, ama süren dış çağrı bitmeden değil (kesilen indirme dosyayı hâlâ açık tutabilir;
    Windows açık dosyayı silmez): silme dış çağrıların sırasına girer. İş ise beklemeden durur."""
    deps = FakeDeps(tmp_path, installed=INST_NEW)  # Ollama kurulu ama kapalı: önce başlatılır (kesilemez)
    deps.part_file.write_bytes(b"MZ")  # önceki bir işten kalan yarım indirme
    deps.hold("ensure_running")
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "ensure_running" in deps.waiting)
    h.svc.cancel()
    assert _wait(qapp, lambda: h.svc.state == "idle")  # iş beklemeden durdu
    qapp.processEvents()
    assert "discard_download" not in deps.calls and deps.part_file.exists()
    deps.release("ensure_running")
    assert _wait(qapp, lambda: "discard_download" in deps.returned)
    assert deps.returned[-2:] == ["ensure_running", "discard_download"] and not deps.part_file.exists()


def test_restart_after_cancel_keeps_download(make, qapp, tmp_path):
    """İptalden hemen sonra yeniden "Kur": süren çağrı bitmeden yeni iş başladıysa kurulum dosyası artık onundur,
    silinmez (yeni iş kaldığı yerden sürer)."""
    deps = FakeDeps(tmp_path, installed=INST_NEW)
    deps.part_file.write_bytes(b"MZ")
    deps.hold("ensure_running")
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: "ensure_running" in deps.waiting)
    h.svc.cancel()
    assert _wait(qapp, lambda: h.svc.state == "idle")
    h.svc.start(h.svc.plan())  # yeni işin dış çağrıları bekleyen silmeden sonra gelir
    deps.release("ensure_running")
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert "discard_download" not in deps.calls and deps.part_file.exists()
    assert h.finished == [(False, ai.TEXT_CANCELLED), (True, ai.TEXT_READY)]


def test_cancel_model_only_download_keeps_setup_file(make, qapp, tmp_path):
    """"Modeli indir" (apply=False, yalnız model) iptal edilince Ollama kurulum dosyasına dokunulmaz: o iş indirmedi."""
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.part_file.write_bytes(b"MZ")
    deps.hold("pull")
    h = make(deps)
    h.svc.start(h.svc.plan("gemma4:e4b", apply=False))
    assert _wait(qapp, lambda: "pull" in deps.waiting)
    h.svc.cancel()
    assert _wait(qapp, lambda: h.svc.state == "idle" and deps.cancelled == ["pull"])
    assert _wait(qapp, lambda: h.svc._helper_done.is_set())
    assert "discard_download" not in deps.calls and deps.part_file.exists()


def test_error_kind_survives_settings_file(make, qapp, tmp_path, monkeypatch):
    """Gerçek ayarlarla (config.Settings): hata metni ve türü dosyaya yazılır, sonraki açılışta "Tekrar dene" ve
    "Elle kur" kararı türden verilir."""
    from dataclasses import fields

    from altyazitr import config

    assert set(vars(_settings().ai)) == {f.name for f in fields(config.AiCfg)}  # testlerin sahte ayarı güncel
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "ayarlar.json")
    s = config.Settings()
    deps = FakeDeps(tmp_path)
    deps.errors["run_setup"] = [mod_error("Ollama kurulumu engellendi.\nAyrıntı: WinError 225", "blocked")]
    h = make(deps, s=s)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    config.save_settings(s)
    s2 = config.load_settings()
    assert (s2.ai.pending_model, s2.ai.failed_kind) == ("gemma4:12b", "blocked")
    h2 = make(FakeDeps(tmp_path), s=s2)
    assert (h2.svc.state, h2.svc.error_kind, h2.svc.can_retry, h2.svc.manual_install) == ("failed", "blocked", True,
                                                                                          True)


def test_failure_is_not_resumed_until_retry(make, qapp, tmp_path):
    error = "Ollama'nın sürümü bu model için eski.\nAyrıntı: HTTP 412"
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.errors["pull"] = [mod_error(error, "outdated", "ollama_runtime")]
    h = make(deps)
    svc = h.svc
    svc.start(svc.plan())
    assert _wait(qapp, lambda: svc.state == "failed")
    assert svc.error == error and svc.message == "Ollama'nın sürümü bu model için eski." and svc.step is P
    assert h.s.ai.failed == error and h.s.ai.failed_kind == "outdated" and h.s.ai.pending_model == "gemma4:12b"
    assert h.saves[-1] == ("quickmt", "translategemma:12b", "gemma4:12b", error)
    assert h.finished == [(False, error)]
    calls = len(deps.calls)
    svc.on_pipeline_running(True)
    svc.on_pipeline_running(False)
    svc.resume_pending()
    qapp.processEvents()
    assert len(deps.calls) == calls and svc.state == "failed"  # kendiliğinden sürmez

    # sonraki açılış: aynı ayarlarla yeni servis hatayı gösterir, yine sürmez; "Tekrar dene" sürdürür
    deps2 = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    h2 = make(deps2, s=h.s)
    assert (h2.svc.state, h2.svc.error, h2.svc.model) == ("failed", error, "gemma4:12b")
    # tür ayarda saklı: "Tekrar dene" (Ollama'yı güncelleyerek) ve "Elle kur" yine sunulur
    assert (h2.svc.error_kind, h2.svc.can_retry, h2.svc.manual_install) == ("outdated", True, True)
    h2.svc.resume_pending()
    assert deps2.calls == []
    h2.svc.retry()
    assert h.s.ai.failed == "" and h.s.ai.failed_kind == ""  # yeniden başlarken hata ve türü silinir
    assert _wait(qapp, lambda: h2.svc.state == "done")
    assert h.s.ai.failed == "" and h.s.translate.engine == "ollama"


def test_outdated_retry_updates_ollama(make, qapp, tmp_path):
    """Model "Ollama'nın sürümü bu model için eski" derse (412) "Tekrar dene" resmî kurulumu uygulamanın sürümüne
    günceller, sonra modeli indirir; "Elle kur" da sunulur."""
    inst = SimpleNamespace(app_exe=INST_NEW.app_exe, version="0.35.0", uninstall="")
    deps = FakeDeps(tmp_path, api=True, version=(0, 35, 0), installed=inst)
    deps.errors["pull"] = [mod_error("Ollama'nın sürümü bu model için eski.\nAyrıntı: HTTP 412", "outdated",
                                     "ollama_runtime")]
    h = make(deps)
    plan = h.svc.plan()
    assert plan.steps == [C, P]  # 0.35 en düşük sürümden yeni: güncellenmez
    h.svc.start(plan)
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert (h.svc.error_kind, h.svc.can_retry, h.svc.manual_install) == ("outdated", True, True)
    h.svc.retry()
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert h.svc.active_plan.steps == [D, I, S, C, P] and h.svc.active_plan.updating
    assert deps.work() == ["inference_compute", "pull", "download_setup", "verify_setup", "run_setup",
                           "inference_compute", "pull"]
    assert not h.s.ai.installed_ollama and h.s.translate.engine == "ollama"


@pytest.mark.parametrize(("fn", "text", "kind", "retry", "manual"), [
    pytest.param("download_setup", "Ollama dosyasının imzası doğrulanamadı; kurulmadı.\nAyrıntı: İmzalayan: Evil Inc.",
                 "signature", False, True, id="imza: yalnız elle kur"),
    pytest.param("run_setup", "Ollama kurulumu engellendi.\nAyrıntı: WinError 225", "blocked", True, True,
                 id="antivirüs"),
    pytest.param("run_setup", "Ollama kurulamadı.\nAyrıntı: Kurulum 4 koduyla bitti.", "install", True, True,
                 id="kurulum hatası"),
    pytest.param("verify_setup", "İndirilen Ollama dosyası bozuk çıktı ve silindi. Tekrar dene'ye basın.",
                 "digest", True, False, id="bozuk dosya"),
    pytest.param("download_setup", f"{ai.TEXT_NETWORK}\nAyrıntı: Son hata: ConnectionError", "network", True, False,
                 id="ağ: indirme kendisi yeniden denedi"),
])
def test_error_kind_decides_retry_and_manual(make, qapp, tmp_path, fn, text, kind, retry, manual):
    deps = FakeDeps(tmp_path)
    deps.errors[fn] = [mod_error(text, kind)]
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert (h.svc.error, h.svc.error_kind) == (text, kind)
    assert (h.svc.can_retry, h.svc.manual_install) == (retry, manual)
    assert deps.count(fn) == 1  # servis bir daha denemez (kurulum dosyasının indirmesi kendi içinde dener)
    assert h.finished == [(False, text)] and (h.s.ai.failed, h.s.ai.failed_kind) == (text, kind)
    # sonraki açılış: tür ayardan okunur, "Tekrar dene" ve "Elle kur" kararı aynı kalır
    h2 = make(FakeDeps(tmp_path), s=h.s)
    assert (h2.svc.state, h2.svc.error, h2.svc.error_kind) == ("failed", text, kind)
    assert (h2.svc.can_retry, h2.svc.manual_install) == (retry, manual)


def test_failure_from_older_settings_without_kind(make, qapp, tmp_path):
    """Türü saklanmamış hata (failed_kind boş): yalnız "Tekrar dene" sunulur, adım bilinmediği için "Elle kur" yok."""
    h = make(FakeDeps(tmp_path), pending_model="gemma4:12b", failed="Ollama kurulamadı.\nAyrıntı: kod 4")
    assert (h.svc.state, h.svc.error_kind, h.svc.can_retry, h.svc.manual_install) == ("failed", "", True, False)


@pytest.mark.parametrize(("text", "kind", "retry"), [
    pytest.param("'gemma4:12b' modeli bulunamadı.\nAyrıntı: file does not exist", "not_found", False,
                 id="model yok"),
    pytest.param("Diskte yer kalmadı (D:). Yer açıp Tekrar dene'ye basın.\nAyrıntı: no space left", "disk", True,
                 id="disk doldu"),
])
def test_model_errors(make, qapp, tmp_path, text, kind, retry):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.errors["pull"] = [mod_error(text, kind, "ollama_runtime")]
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert (h.svc.error, h.svc.error_kind, h.svc.can_retry, h.svc.manual_install) == (text, kind, retry, False)
    assert deps.count("pull") == 1


def test_cancel_after_failure_dismisses_it(make, qapp, tmp_path):
    """Hatadan sonra "Hızlı çeviride kal": yarım iş unutulur, ağ hatasında kalan yarım kurulum dosyası da silinir."""
    deps = FakeDeps(tmp_path)
    deps.part_file.write_bytes(b"MZ")  # önceki açılışta ağ hatasıyla yarım kalan indirme
    h = make(deps, pending_model="gemma4:12b", failed=f"{ai.TEXT_NETWORK}\nAyrıntı: Son hata", failed_kind="network")
    assert (h.svc.state, h.svc.error_kind) == ("failed", "network")
    h.svc.cancel()
    assert (h.s.ai.pending_model, h.s.ai.failed, h.s.ai.failed_kind, h.svc.state) == ("", "", "", "idle")
    assert h.saves and h.finished == []
    assert _wait(qapp, lambda: "discard_download" in deps.returned) and not deps.part_file.exists()


def test_resume_pending(make, qapp, tmp_path):
    """Yarıda kapanan iş sonraki açılışta kaldığı yerden sürer (Ollama kurulu ama kapalı: başlatılır). Plan arka
    planda çıkarılır: Ollama kapalıyken yoklama ~1,5 sn sürer, arayüz beklemez."""
    deps = FakeDeps(tmp_path, installed=INST_NEW)
    h = make(deps, pending_model="gemma4:e4b")
    assert h.svc.state == "idle"
    h.svc.resume_pending()
    assert h.svc.state == "running" and h.svc.active_plan is None
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert h.svc.active_plan.steps == [S, C, P]
    assert deps.work() == ["ensure_running", "inference_compute", "pull"] and deps.timeouts == [25.0]
    assert deps.pulls == ["gemma4:e4b"] and h.s.translate.ollama_model == "gemma4:e4b"
    assert h.s.ai.pending_model == ""

    idle = FakeDeps(tmp_path)
    h2 = make(idle)  # bekleyen iş yok
    h2.svc.resume_pending()
    assert idle.calls == [] and h2.svc.state == "idle"


def test_blocked_plan_fails_at_once(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path, other_on_port=True)
    h = make(deps)
    plan = h.svc.plan()
    assert plan.blocker == "11434 portunu başka bir program kullanıyor."
    h.svc.start(plan)
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert h.finished == [(False, plan.blocker)] and h.s.ai.failed == plan.blocker and deps.work() == []
    assert (h.svc.error_kind, h.svc.can_retry, h.svc.manual_install) == ("port", True, False)


# --- GPU kapısı --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("computes", [[CPU], [IGPU, CPU]], ids=["yalnız işlemci", "yalnız tümleşik kart"])
def test_gpu_gate_stops_before_model_download(make, qapp, tmp_path, computes):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW, computes=computes)
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert h.svc.error == f"{ai.TEXT_NO_GPU}\n{ai.TEXT_NO_GPU_TIP}" and h.svc.step is C
    assert (h.svc.error_kind, h.svc.can_retry, h.svc.manual_install) == ("no_gpu", True, False)
    assert "pull" not in deps.calls


@pytest.mark.parametrize(("computes", "model"), [
    pytest.param([GPU_8], "gemma4:e4b", id="8 GB kart: E4B"),
    pytest.param([IGPU, GPU_12], "gemma4:12b", id="12 GB kart: 12B"),
    pytest.param([], "gemma4:12b", id="günlük okunamadı: kapı atlanır"),
])
def test_gpu_gate_picks_model_for_vram(make, qapp, tmp_path, computes, model):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW, computes=computes)
    h = make(deps)
    h.svc.start(h.svc.plan("gemma4:12b"))
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.pulls == [model] and h.svc.model == model and h.s.translate.ollama_model == model
    if model == "gemma4:e4b":
        assert h.svc.note == "Ekran kartının belleği 8 GB: daha hafif Gemma 4 E4B kurulacak."
        assert ("quickmt", "translategemma:12b", "gemma4:e4b", "") in h.saves  # yarıda kalırsa E4B sürer
        assert h.steps[-1] is P and h.svc.all_total_bytes == ai.MODEL_INFO["gemma4:e4b"].size
    else:
        assert h.svc.note == ""


# --- ağ hataları -------------------------------------------------------------------------------------------------
def test_network_error_is_retried(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.errors["pull"] = [mod_error(f"{ai.TEXT_NETWORK}\nAyrıntı: dial tcp", "network", "ollama_runtime"),
                           ConnectionError("bağlantı koptu")]
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.count("pull") == 3 and h.finished == [(True, ai.TEXT_READY)]


def test_network_error_fails_after_retries(make, qapp, tmp_path):
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.errors["pull"] = [ConnectionError("bağlantı koptu") for _ in range(4)]
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: h.svc.state == "failed")
    assert deps.count("pull") == 1 + len(ai.RETRY_DELAYS)
    assert h.svc.message == ai.TEXT_NETWORK and h.s.ai.failed.startswith(ai.TEXT_NETWORK)
    assert (h.svc.error_kind, h.svc.can_retry) == ("network", True)


# --- hız testi (Faz 2) ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("verdict", "ms", "share", "engine", "text"), [
    pytest.param("ok", 410, 1.0, "ollama", ai.TEXT_READY, id="hızlı: motor yapay zekâ olur"),
    pytest.param("slow", 2600, 0.6, "quickmt",
                 "Yapay zekâ bu bilgisayarda yavaş\nSatır başına ~2,6 sn: ekran kartının belleği yetmiyor. "
                 "Hızlı çeviride kalabilir ya da hafif modeli kurabilirsiniz.", id="yavaş: motor değişmez"),
    pytest.param("slow", 2600, 1.0, "quickmt",
                 "Yapay zekâ bu bilgisayarda yavaş\nSatır başına ~2,6 sn. "
                 "Hızlı çeviride kalabilir ya da hafif modeli kurabilirsiniz.", id="yavaş, pay 1,0: neden söylenmez"),
])
def test_speed_test_verdict_decides(make, qapp, tmp_path, verdict, ms, share, engine, text):
    """Karar yalnız Speed.verdict / median_ms'ten: gpu_share (Ollama'da güvenilmez) en çok açıklamaya girer."""
    deps = FakeDeps(tmp_path, api=True, version=NEW, installed=INST_NEW)
    deps.speed = SimpleNamespace(median_ms=ms, p90_ms=ms + 100, gpu_share=share, verdict=verdict)
    h = make(deps)
    plan = h.svc.plan(test=True)
    assert plan.steps == [C, P, T]
    h.svc.start(plan)
    assert _wait(qapp, lambda: h.svc.state == "done")
    assert deps.work() == ["inference_compute", "pull", "speed_test"]
    assert deps.speed_args == (ai.DEFAULT_URL, "gemma4:12b", "gta6")
    assert (h.s.ai.speed_ms, h.svc.speed_verdict, h.s.translate.engine) == (ms, verdict, engine)
    assert h.s.ai.pending_model == "" and h.finished == [(True, text)]


# --- kapanış ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["download_setup", "run_setup"], ids=["indirme kesilir", "kurulum sürer"])
def test_shutdown_stops_at_once_and_keeps_pending(make, qapp, tmp_path, name):
    """Kapanışta iş parçacığı beklemeden durur: indirme isteği kesilir, kurulum süreci öldürülmez (kendi biter).
    pending_model kalır: iş sonraki açılışta sürer. finished yayılmaz."""
    deps = FakeDeps(tmp_path)
    deps.hold(name)
    h = make(deps)
    h.svc.start(h.svc.plan())
    assert _wait(qapp, lambda: name in deps.waiting)
    assert h.svc.shutdown(3000)
    assert not h.svc._thread.isRunning()
    if name == "run_setup":
        assert "run_setup" in deps.waiting and "run_setup" not in deps.returned and deps.cancelled == []
        deps.release("run_setup")
        assert _wait(qapp, lambda: "run_setup" in deps.returned)
    else:
        assert _wait(qapp, lambda: deps.cancelled == ["download_setup"])
        assert deps.part_file.exists()  # yarım indirme kalır: sonraki açılışta kaldığı yerden sürer
    qapp.processEvents()
    assert h.finished == [] and "discard_download" not in deps.calls
    assert (h.s.ai.pending_model, h.s.ai.failed) == ("gemma4:12b", "")
    h.svc.start(h.svc.plan())  # kapandıktan sonra yeni iş başlamaz
    assert not h.svc._thread.isRunning() and deps.count(name) == 1


# --- yardımcılar -----------------------------------------------------------------------------------------------
def test_progress_eta_uses_last_ten_seconds():
    t = [0.0]
    p = ai._Progress({D: 1000, P: 9000}, lambda: t[0])
    p.begin(D)
    snap = None
    for sec in range(21):  # 20 sn boyunca saniyede 10 bayt
        t[0] = float(sec)
        snap = p.update(10 * sec, 1000)
    assert snap == (200, 1000, 200, 10_000, 980)
    for sec in range(21, 32):  # hız saniyede 50 bayta çıktı: kalan süre yalnız son ~10 sn'den
        t[0] = float(sec)
        snap = p.update(200 + 50 * (sec - 20), 1000)
    assert snap == (750, 1000, 750, 10_000, 185)
    p.finish(D)
    p.begin(P)
    assert p.snapshot()[:4] == (0, 9000, 1000, 10_000)
    t[0] += 1
    assert p.update(50, 100)[:4] == (50, 9000, 1050, 10_000)  # bildirilen toplam beklenenden küçük: beklenen kalır
    p.skip(P)  # model zaten varmış
    assert (p.all_done, p.all_total) == (1000, 1000)


def test_progress_notifications_are_limited():
    t = [100.0]
    p = ai._Progress({P: 1000}, lambda: t[0])
    p.begin(P)
    sent = [p.update(i, 1000) for i in range(1, 101)]  # aynı anda 100 bildirim
    assert sum(s is not None for s in sent) == 1
    t[0] += 0.125
    assert p.update(150, 1000) is None
    t[0] += 0.125  # 0,2 sn'yi geçti
    assert p.update(200, 1000) is not None
    assert p.update(1000, 1000) is not None  # adım bitti: her zaman bildirilir


def test_error_info():
    bad = "İndirilen Ollama dosyası bozuk çıktı ve silindi. Tekrar dene'ye basın."
    assert ai.error_info(mod_error(bad, "digest")) == (bad, "digest")  # modülün Türkçe iletisi ve türü olduğu gibi
    assert ai.error_info(AiSetupError("Ollama başlatılamadı.", "start")) == ("Ollama başlatılamadı.", "start")
    assert ai.error_info(OSError(28, "No space left on device"), "D:") == (
        "Diskte yer kalmadı (D:). Yer açıp Tekrar dene'ye basın.\nAyrıntı: OSError: [Errno 28] No space left on device",
        "disk")
    assert ai.error_info(ConnectionError("koptu")) == (f"{ai.TEXT_NETWORK}\nAyrıntı: ConnectionError: koptu",
                                                       "network")
    assert ai.error_info(ValueError("bozuk")) == ("Yapay zekâ kurulamadı.\nAyrıntı: ValueError: bozuk", "")
    assert ai.is_network_error(mod_error("Ollama'yla bağlantı koptu.", "network", "ollama_runtime"))
    assert not ai.is_network_error(mod_error(bad, "digest"))


def test_helpers():
    assert ai.local_url("http://localhost:11434") == ai.DEFAULT_URL
    assert ai.local_url("0.0.0.0:11500") == "http://127.0.0.1:11500"  # OLLAMA_HOST biçimi
    assert ai.local_url("http://192.168.1.5:11434") == ai.DEFAULT_URL  # başka bilgisayara istek atılmaz
    assert ai.has_model("gemma4:12b", ["gemma4:12b"]) and ai.has_model("llama3", ["llama3:latest"])
    assert not ai.has_model("gemma4:12b", ["gemma4:e4b", "gemma4:12b-q8"])
    assert ai.model_for_vram("gemma4:12b", 10 * GIB) == "gemma4:e4b"
    assert ai.model_for_vram("gemma4:12b", GPU_12.total) == "gemma4:12b"
    assert ai.model_for_vram("gemma4:e4b", 6 * GIB) == "gemma4:e4b"
    assert ai.step_title(P, "gemma4:12b") == "Gemma 4 12B indiriliyor"
    assert ai.step_title(I, update=True) == "Ollama güncelleniyor"
    assert ai.parse_version("ollama version is 0.40.16") == NEW and ai.parse_version("v0.12") == (0, 12, 0)
    assert ai.parse_version("?") is None
