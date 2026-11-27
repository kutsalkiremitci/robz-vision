"""Başla sayfası (gui/tv_home.py): adımların durumu, sıradaki adım, karoların açtığı yerler, ok tuşlarıyla gezinme ve
Başlat düğmesi. Sahte bağlam; Qt 'offscreen', pencereler ekranda açılmaz."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from altyazitr.config import Settings, new_settings


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([])
    return app


def _pump(app, sec: float = 0.05) -> None:
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _ctx(settings: Settings | None = None, obs: int = 0):
    from PySide6.QtCore import QObject, Signal

    class Pipe(QObject):
        running_changed = Signal(bool)
        running = False

    class Ai(QObject):
        changed = Signal()
        state = "idle"

    class Ctx(QObject):
        obs_clients = Signal(int)

    ctx = Ctx()
    ctx.s = settings or new_settings()
    ctx.pipeline, ctx.ai = Pipe(), Ai()
    ctx.server = SimpleNamespace(url="http://127.0.0.1:8765/", obs_clients=[object()] * obs)
    ctx.tests = []
    ctx.display = SimpleNamespace(show_test=lambda: ctx.tests.append(1))
    ctx.toggles = []
    ctx.toggle_running = lambda: ctx.toggles.append(1)
    return ctx


_PAGES: list = []


@pytest.fixture
def start_page(qapp, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent, Qt

    from altyazitr.gui import tv_home

    monkeypatch.setattr("altyazitr.translate.quickmt_downloaded", lambda: True)

    def make(ctx=None):
        ctx = ctx or _ctx()
        calls: list = []
        win = SimpleNamespace(ctx=ctx, goto=lambda k: calls.append(("goto", k)),
                              select_region=lambda: calls.append(("region",)),
                              start_tour=lambda: calls.append(("tour",)))
        pg = tv_home.StartPage(win)
        pg.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        pg.resize(1228, 900)
        pg.show()
        _pump(qapp, 0.1)
        _PAGES.append(pg)
        return pg, ctx, calls

    yield make
    for pg in _PAGES:
        pg.close()
        pg.deleteLater()
    _PAGES.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_yeni_kurulumda_adimlar_sirayla_gosterilir(monkeypatch):
    """Yeni kurulumda varsayılanlar hazır olsa da adımlar kendiliğinden "tamam" sayılmaz: kullanıcı rehberde her
    adımı bir kez görür ("kontrol edin"). Onaylanınca tamam; OBS isteğe bağlıdır."""
    from altyazitr.gui import tv_home

    monkeypatch.setattr("altyazitr.translate.quickmt_downloaded", lambda: True)
    s = new_settings()
    s.capture.rect = [380, 870, 1233, 212]
    states = tv_home.step_states(_ctx(s))
    assert [st.key for st in states] == ["source", "region", "translate", "style", "obs"]
    assert [st.number for st in states] == [1, 2, 3, 4, 5]
    assert not any(st.done for st in states) and states[4].optional
    assert states[1].status == "1233 × 212 bölge seçili · kontrol edin"
    assert tv_home.next_step(states).key == "source"
    s.guide_done = ["source", "region", "translate", "style"]
    states = tv_home.step_states(_ctx(s))
    assert all(st.done for st in states[:4]) and states[3].status == "Oyun altyazısının üzerine"
    assert tv_home.next_step(states) is None  # OBS isteğe bağlı: hazır


@pytest.mark.parametrize("change, key, status", [
    (lambda s: (setattr(s.capture, "mode", "window"), setattr(s.capture, "window_title", "")), "source",
     "Pencere seçilmedi"),
    (lambda s: setattr(s.capture, "rect", []), "region", "Altyazının yeri seçilmedi"),
])
def test_eksik_adim_siradaki_olur(monkeypatch, change, key, status):
    from altyazitr.gui import tv_home

    monkeypatch.setattr("altyazitr.translate.quickmt_downloaded", lambda: True)
    s = new_settings()
    s.capture.rect = [380, 870, 1233, 212]
    s.guide_done = ["source", "region", "translate", "style"]  # hepsi onaylı; yine de eksik olan sıradaki olur
    change(s)
    nxt = tv_home.next_step(tv_home.step_states(_ctx(s)))
    assert nxt is not None and nxt.key == key and nxt.status == status


def test_ceviri_motoru_metni(monkeypatch):
    from altyazitr.gui import tv_home

    s = new_settings()
    s.guide_done = ["source", "region", "translate", "style"]
    monkeypatch.setattr("altyazitr.translate.quickmt_downloaded", lambda: False)
    st = {x.key: x for x in tv_home.step_states(_ctx(s))}["translate"]
    assert not st.done and st.status == "Çeviri modeli inmedi"
    s.translate.engine, s.translate.ollama_model = "ollama", "gemma4:12b"
    st = {x.key: x for x in tv_home.step_states(_ctx(s))}["translate"]
    assert st.done and st.status == "Yapay zekâ · gemma4:12b"
    ctx = _ctx(s)
    ctx.ai.state = "running"
    assert {x.key: x for x in tv_home.step_states(ctx)}["translate"].status == "Yapay zekâ kuruluyor…"


def test_karolar_ilgili_yeri_acar(start_page, monkeypatch):
    import altyazitr.gui.dialogs as dialogs

    shown: list = []
    monkeypatch.setattr(dialogs, "show_obs_help", lambda parent, url: shown.append(url))
    pg, ctx, calls = start_page()
    for key in ("source", "region", "translate", "style", "obs"):
        pg.tiles[key].click()
    assert calls == [("goto", "home"), ("region",), ("goto", "translate"), ("goto", "style")]
    assert shown == ["http://127.0.0.1:8765/"]
    pg.help_tiles[0].click()
    assert calls[-1] == ("tour",)
    pg.start.click()
    pg.test.click()
    assert ctx.toggles == [1] and ctx.tests == [1]


def test_pencere_secilmeden_bolge_karosu_kaynaga_goturur(start_page):
    s = new_settings()
    s.capture.mode, s.capture.window_title, s.capture.window_exe = "window", "", ""
    pg, ctx, calls = start_page(_ctx(s))
    assert pg.banner.kind == "next" and pg.banner.button.isVisibleTo(pg)
    assert pg.tiles["source"].next and not pg.tiles["source"].done
    pg.tiles["region"].click()
    assert calls == [("goto", "home")]
    pg.banner.button.click()  # Şimdi yap: sıradaki adım (kaynak)
    assert calls[-1] == ("goto", "home")


def test_hazirken_serit_ve_baslat_dugmesi(start_page, qapp):
    from altyazitr.gui import tv_home

    s = new_settings()
    s.capture.rect = [380, 870, 1233, 212]
    s.guide_done = ["source", "region", "translate", "style"]
    pg, ctx, _calls = start_page(_ctx(s, obs=1))
    assert pg.banner.kind == "ready" and pg.banner.text.text() == tv_home.READY_TEXT
    assert not pg.banner.button.isVisibleTo(pg)
    assert pg.tiles["obs"].done and pg.tiles["obs"].status == "OBS bağlı"
    assert pg.start.text() == tv_home.START_TEXT
    ctx.pipeline.running = True
    ctx.pipeline.running_changed.emit(True)
    _pump(qapp)
    assert pg.start.text() == tv_home.STOP_TEXT and pg.banner.kind == "running"
    assert pg.status_card.running


def test_ok_tuslariyla_gezinme(start_page):
    pg, _ctx_, _calls = start_page()
    t = pg.tiles
    assert pg._neighbour(t["source"], 1, 0) is t["region"]
    assert pg._neighbour(t["region"], -1, 0) is t["source"]
    assert pg._neighbour(t["source"], -1, 0) is None  # solda bir şey yok
    assert pg._neighbour(t["translate"], 0, 1) in (pg.banner.button, *pg.help_tiles)  # aşağı: adım kutusu/yardım
    assert pg._neighbour(t["source"], 0, -1) in (pg.start, pg.test, pg.banner.button)


def test_menude_ilk_sayfa_basla():
    import altyazitr.gui.main_window as mw

    assert mw.PAGES[0][0] == "start" and mw.PAGES[0][1] == "Başla" and mw.NAV_ICONS["start"] == "play-circle"
    assert [k for k, _t, _c in mw.PAGES] == ["start", "home", "style", "translate", "settings"]


def test_kart_rehberi_baslatir(start_page):
    """Ana pencere rehberi destekliyorsa karo, sayfayı rehber şeridiyle açar (start_guide)."""
    pg, ctx, calls = start_page()
    pg.win.start_guide = lambda key: calls.append(("guide", key))
    pg.tiles["region"].click()
    pg.banner.button.click()  # Şimdi yap: yeni kurulumda ilk adım
    assert calls == [("guide", "region"), ("guide", "source")]


def test_rehber_sirasi():
    from altyazitr.gui import tour, tv_home

    assert tv_home.GUIDE_ORDER == ("source", "region", "translate", "style")
    assert {k: v[0] for k, v in tv_home.GUIDE.items()} == {"source": "home", "region": "home",
                                                          "translate": "translate", "style": "style"}
    assert "chiaki" not in tv_home.GUIDE["source"][2].lower() and "Robz Play" in tv_home.GUIDE["source"][2]
    # sihirbazın parçaları adımların sırasıyla gelir; her parçanın sayfası adımın sayfasıdır
    groups = [s.group for s in tour.WIZARD_STEPS if s.group]
    assert list(dict.fromkeys(groups)) == list(tv_home.GUIDE_ORDER)
    assert all(s.page == tv_home.GUIDE[s.group][0] for s in tour.WIZARD_STEPS if s.group)
    assert tour.WIZARD_STEPS[-1].group == "" and tour.WIZARD_STEPS[-1].page == "start"


def test_sihirbaz_kutusu_siradaki_adimi_anlatir(start_page, qapp):
    """Adım çizgisinin altındaki kutu sıradaki adımı anlatır ("Adım n / 4", ad, ne yapılacağı, şu anki durum) ve
    çentiğiyle çizgideki düğümü gösterir. Çizgide ve kutuda "kontrol edin" eki yazılmaz."""
    from altyazitr.gui import tv_home

    s = new_settings()
    s.capture.rect = [380, 870, 1233, 212]
    pg, ctx, _calls = start_page(_ctx(s))
    b = pg.banner
    assert b.kind == "next" and b.eyebrow.text() == "Adım 1 / 4" and b.title.text() == "Oyunu göster"
    assert b.text.text() == tv_home.GUIDE["source"][2] and b.status.text() == "Şu an: Tüm ekran okunuyor"
    assert b.anchor is pg.tiles["source"] and b.button.isVisibleTo(pg)
    assert b.button.text() == tv_home.START_SETUP  # hiçbir adım onaylanmadı: "Kuruluma başla"
    assert pg.progress.text().startswith("Tamamlanan adım: 0 / 4")
    assert pg.tiles["region"].status == "1233 × 212 bölge seçili"
    s.guide_done = ["source"]
    pg.refresh()
    _pump(qapp)
    assert pg.tiles["source"].done and pg.tiles["region"].next and b.anchor is pg.tiles["region"]
    assert b.eyebrow.text() == "Adım 2 / 4" and pg.progress.text().startswith("Tamamlanan adım: 1 / 4")
    assert b.button.text() == tv_home.DO_IT
    pg.grab()  # çentikli kutu ve çizgi hatasız çizilir
