"""Model/ekran gerektirmeyen birim testleri:  python -m pytest -q"""

import time

import cv2
import numpy as np
import pytest

from altyazitr.config import Settings, _from_dict
from altyazitr.detect import ChangeGate, GateParams, text_mask
from altyazitr.english import english_mass, english_score
from altyazitr.hotkeys import parse_hotkey
from altyazitr.ocr import OcrLine, outlined_text_mask
from altyazitr.pipeline import LatestSlot, Tracker
from altyazitr.server import style_vars
from altyazitr.textproc import assemble, fix_ocr, is_valid, norm_key, split_sentences, split_speaker
from altyazitr.translate import (Glossary, TranslationCache, _clean_llm_output, _sentence_case_if_shouting,
                                 restore_end_punctuation)


# --- metin işleme ------------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("| told you", "I told you"),
    ("l'm here", "I'm here"),
    ("Where are you , Jason ?", "Where are you, Jason?"),
    ("G0OD", "GOOD"),
    ("Wait…  what", "Wait... what"),
    ("’cause", "'cause"),
])
def test_fix_ocr(raw, expected):
    assert fix_ocr(raw) == expected


def test_norm_key():
    assert norm_key("Hey, Jason!  You OK?") == "hey jason you ok"


def test_split_speaker():
    assert split_speaker("LUCIA: Get in the car.") == ("LUCIA", "Get in the car.")
    assert split_speaker("Big Mike: Yo.") == ("Big Mike", "Yo.")
    assert split_speaker("Objective: Go to the bank.") == (None, "Objective: Go to the bank.")
    assert split_speaker("Mission failed: you died") == (None, "Mission failed: you died")
    assert split_speaker("We meet at 10:30 tonight") == (None, "We meet at 10:30 tonight")


def test_assemble_rows_and_blocks():
    lines = [
        OcrLine("HUD 120$", None, 10, 0, 60, 10),  # üstte ayrı blok (HUD)
        OcrLine("They're watching", None, 100, 60, 200, 20),
        OcrLine("us. We need", None, 310, 61, 120, 20),  # aynı satırın ikinci parçası
        OcrLine("to move, now!", None, 150, 84, 200, 20),
    ]
    text, speaker, _ = assemble(lines)
    assert text == "They're watching us. We need to move, now!"
    assert speaker is None
    text_all, _, _ = assemble(lines, keep_lowest_block=False)
    assert text_all.startswith("HUD")


def test_assemble_speaker_and_hyphen():
    lines = [OcrLine("JASON: we gotta re-", None, 0, 0, 100, 20), OcrLine("treat now", None, 0, 22, 100, 20)]
    text, speaker, _ = assemble(lines)
    assert speaker == "JASON"
    assert text == "we gotta retreat now"


def test_is_valid():
    assert is_valid("Get in the car.", [])
    assert not is_valid("~~ |", [])
    assert not is_valid("1234 5678", [])
    assert not is_valid("Get in the car", [0.2, 0.3])


def test_split_sentences():
    assert split_sentences("Hey bro, you good? That cop's tailing us.") == ["Hey bro, you good?", "That cop's tailing us."]
    assert split_sentences("Mr. Smith said he left. Really?") == ["Mr. Smith said he left.", "Really?"]
    assert split_sentences("Wait... Is that him?") == ["Wait...", "Is that him?"]
    assert split_sentences("No split here") == ["No split here"]


# --- sözlük / önbellek / LLM çıktısı -----------------------------------------------
def test_glossary_protect_restore():
    g = Glossary([["Vice City", ""], ["cop", "polis"], ["", "x"]])
    # yalnızca çevrilmeyecek adlar korunur, 'cop' NMT'ye olduğu gibi gider
    prot, mapping = g.protect("The cop drove to vice city.")
    assert "cop" in prot and "vice city" not in prot.lower() and list(mapping.values()) == ["Vice City"]
    assert Glossary.restore(prot, mapping) == "The cop drove to Vice City."
    # model boşluk ekleyip küçük harf yapsa da geri yüklenmeli
    tok = next(iter(mapping))
    assert Glossary.restore(f"{tok.lower()[:2]} {tok[2:]}'e gittik", {tok: "Vice City"}) == "Vice City'e gittik"
    # LLM'lere sözlük talimat olarak gider
    assert "Vice City = Vice City (do not translate)" in g.prompt_lines()
    assert "cop = polis" in g.prompt_lines()


def test_cache_exact_and_fuzzy():
    c = TranslationCache()
    c.put("they re watching us we need to move now", "Bizi izliyorlar.")
    assert c.get("they re watching us we need to move now") == "Bizi izliyorlar."
    assert c.get("they re watching us we need to move nov") == "Bizi izliyorlar."
    assert c.get("completely different line here") is None


def test_clean_llm_output():
    assert _clean_llm_output('"Bizi izliyorlar."') == "Bizi izliyorlar."
    assert _clean_llm_output("<think>hmm</think>\nTürkçe: Hadi gidelim!") == "Hadi gidelim!"
    assert _clean_llm_output("Satır bir\nSatır iki\nSatır üç") == "Satır bir Satır iki"


def test_restore_end_punctuation():
    assert restore_end_punctuation("Come on, man!", "Hadi ama dostum.") == "Hadi ama dostum!"
    assert restore_end_punctuation("You good?", "İyi misin?") == "İyi misin?"
    assert restore_end_punctuation("Wait...", "Bekle.") == "Bekle..."
    assert restore_end_punctuation("Go now.", "Hemen git!") == "Hemen git!"  # nokta ile bitiyorsa dokunma


def test_cache_persistence(tmp_path):
    c = TranslationCache()
    c.put("hello there", "Merhaba")
    p = tmp_path / "onbellek.json"
    c.save(p)
    c2 = TranslationCache()
    c2.load(p)
    assert c2.get("hello there") == "Merhaba"


def test_shouting_to_sentence_case():
    assert _sentence_case_if_shouting("GET IN THE CAR. I SAID NOW!") == "Get in the car. I said now!"
    assert _sentence_case_if_shouting("Normal text") == "Normal text"


# --- takipçi ----------------------------------------------------------------------
def test_tracker_dedup_extension_and_reappear():
    tr = Tracker()
    a = tr.on_text("They're watching us.", None, 0.0)
    assert a and a["seq"] == 1
    assert tr.on_text("They're watchinq us.", None, 0.3) is None  # OCR titremesi
    b = tr.on_text("They're watching us. We need to move, now!", None, 0.6)
    assert b and b["replace"]  # metin uzadı
    tr.on_gone(1.0)
    assert tr.on_text("They're watching us. We need to move, now!", None, 1.3) is None  # kısa süre sonra geri geldi
    tr.on_gone(2.0)
    c = tr.on_text("They're watching us. We need to move, now!", None, 5.0)  # uzun süre sonra tekrar söylendi
    assert c and c["seq"] == 3


def test_tracker_upgrades_to_better_reading():
    tr = Tracker()
    a = tr.on_text("Get in theccar. We're going to Vice City.", "LUCIA", 0.0, english_score("Get in theccar. We're going to Vice City."))
    assert a
    better = "Get in the car. We're going to Vice City."
    b = tr.on_text(better, "LUCIA", 0.5, english_score(better))
    assert b and b["replace"] and b["seq"] == 2
    # aynı kalitede küçük titreme -> yeni olay yok
    assert tr.on_text("Get in the car. We're going to Vice City", "LUCIA", 0.8, english_score(better)) is None


def test_english_score():
    good = english_score("They're watching us. We need to move, now!")
    bad = english_score("LUCIA:UGet imthe ear. Mission faileg;Y6u")
    assert good > 0.9 and bad < 0.6
    assert english_score("Lucia drove to Vice City.") > 0.8  # özel adlar cezalandırılmaz
    assert not is_valid("Xq7 zzkr 9fj$ wq", [], 3, english_score("Xq7 zzkr 9fj$ wq"))


def test_english_mass_prefers_complete_reading():
    full = "They're watching us. We need to move, now!"
    partial = "They're need to move, now!"  # kelime düşmüş ama tamamen İngilizce
    noisy = "They're watching us. We need to move, now! x7#q zzv"
    assert english_score(partial) >= 0.9  # temizlik ölçüsü farkı göremez...
    assert english_mass(full) > english_mass(partial)  # ...kapsam ölçüsü görür
    assert english_mass(noisy) == pytest.approx(english_mass(full))  # çöp kapsamı artırmaz


def _outlined_subtitle_on_gravel(seed: int = 3) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Kumlu gri zemin (parlak taneler + sarı çizgi) üzerinde siyah kenarlı beyaz altyazı."""
    img = _write(_gravel(seed), "Get in the car", (255, 255, 255), org=(200, 75))
    (w, h), base = cv2.getTextSize("Get in the car", cv2.FONT_HERSHEY_DUPLEX, 1.4, 2)
    return img, (200 - 8, 75 - h - 8, 200 + w + 8, 75 + base + 8)


def test_outlined_text_mask_drops_gravel_keeps_text():
    img, (x0, y0, x1, y1) = _outlined_subtitle_on_gravel()
    m = outlined_text_mask(img) > 0
    inside = m[y0:y1, x0:x1]
    outside = m.copy()
    outside[y0:y1, x0:x1] = False
    assert inside.mean() > 0.05  # harfler kaldı
    assert outside.sum() < 0.02 * inside.sum()  # taneler ve sarı çizgi gitti
    # karşılaştırma: düz parlaklık eşiği aynı karede bol gürültü bırakır
    bright = cv2.inRange(cv2.cvtColor(img, cv2.COLOR_BGR2HSV), (0, 0, 185), (180, 60, 255)) > 0
    bright[y0:y1, x0:x1] = False
    assert bright.sum() > 10 * max(outside.sum(), 1)


def test_outlined_text_mask_empty_frame():
    assert not outlined_text_mask(np.full((60, 200, 3), 90, np.uint8)).any()


def _gravel(seed: int, h: int = 120, w: int = 900) -> np.ndarray:
    rng = np.random.default_rng(seed)
    g = np.clip(rng.normal(130, 20, (h, w)), 0, 255).astype(np.uint8)
    specks = rng.random(g.shape) < 0.04
    g[specks] = rng.integers(190, 240, specks.sum()).astype(np.uint8)
    img = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    cv2.line(img, (40, h - 1), (140, 0), (60, 200, 225), 12)  # sarı yol çizgisi
    return img


def _write(img: np.ndarray, text: str, bgr: tuple[int, int, int], org=(160, 75)) -> np.ndarray:
    """Oyun altyazısı gibi: renkli harf + ~2 px koyu dış çizgi. (İki kez putText ile çizmek dış çizgiyi
    vermiyor: ince renkli çizgi alttaki kalın siyahın tamamını örtüyor.)"""
    m = np.zeros(img.shape[:2], np.uint8)
    cv2.putText(m, text, org, cv2.FONT_HERSHEY_DUPLEX, 1.4, 255, 2, cv2.LINE_AA)
    letters = m > 127
    img[cv2.dilate(letters.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = (0, 0, 0)
    img[letters] = bgr
    return img


# Yazının varlığı renge bağlı değil: kırmızı, mavi, mor, yeşil, beyaz hepsi "yazı var"
@pytest.mark.parametrize("bgr", [(50, 50, 224), (229, 182, 93), (230, 110, 155), (114, 204, 114), (255, 255, 255)])
def test_text_mask_detects_any_color(bgr):
    p = GateParams()
    on_dark = _write(np.full((120, 900, 3), 35, np.uint8), "Follow the Tennis Coach", bgr)
    on_gravel = _write(_gravel(5), "Follow the Tennis Coach", bgr)
    assert text_mask(on_dark, p.bright).mean() >= p.appear
    assert text_mask(on_gravel, p.bright).mean() >= p.appear


def test_text_mask_ignores_gravel_and_lights():
    """Yazısız kumlu asfalt / yol çizgisi ve koyu zeminde dolu parlak lekeler (lamba, far) yazı sayılmaz."""
    p = GateParams()
    for seed in range(4):
        assert text_mask(_gravel(seed), p.bright).mean() < p.vanish
    lights = np.full((120, 900, 3), 30, np.uint8)
    for x, col in ((150, (255, 255, 255)), (450, (60, 200, 255)), (750, (255, 120, 80))):
        cv2.ellipse(lights, (x, 60), (60, 35), 0, 0, 360, col, -1)
    assert text_mask(lights, p.bright).mean() < p.vanish


def test_text_mask_stable_on_moving_gravel():
    """Aynı yazı, akan asfalt: maske değişmemeli (yoksa yazı hiç 'sabit' sayılmaz, okuma gecikir)."""
    from altyazitr.detect import jaccard_distance
    a = text_mask(_write(_gravel(1), "We need to move, now!", (255, 255, 255)))
    b = text_mask(_write(_gravel(2), "We need to move, now!", (255, 255, 255)))
    assert jaccard_distance(a, b) < GateParams().still


def test_outline_preprocess_keeps_colored_words():
    from altyazitr.ocr import preprocess
    img = _write(np.full((120, 900, 3), 35, np.uint8), "Follow the", (255, 255, 255), org=(60, 75))
    img = _write(img, "Tennis Coach", (229, 182, 93), org=(330, 75))  # mavi vurgu
    page, _ = preprocess(img, "1", "outline")
    ink = page[12:-12, 12:-12, 0] < 128
    assert ink[:, 330:].sum() > 0.5 * ink[:, :330].sum()  # renkli kelimeler de sayfada


def test_fix_ocr_noise_chars():
    assert fix_ocr("We need_to move•now") == "We need to move now"
    assert split_speaker("LUCIA:Get in.") == ("LUCIA", "Get in.")


def test_latest_slot_overwrites():
    s = LatestSlot()
    s.put(1)
    s.put(2)
    assert s.get(0.01) == 2
    assert s.get(0.01) is None


# --- değişim algılama ---------------------------------------------------------------
def _frame(text: str | None, shift: int = 0) -> np.ndarray:
    rng = np.random.default_rng(shift)
    img = cv2.GaussianBlur((rng.random((200, 1200, 3)) * 120).astype(np.uint8), (0, 0), 8)
    cv2.circle(img, (200 + shift * 30, 80), 90, (235, 235, 235), -1)  # büyük parlak alan (gökyüzü/ışık)
    if text:
        cv2.putText(img, text, (150, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 8, cv2.LINE_AA)
        cv2.putText(img, text, (150, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3, cv2.LINE_AA)
    return img


def test_text_mask_ignores_large_bright_blobs():
    assert text_mask(_frame(None)).mean() < 0.003
    assert text_mask(_frame("They're watching us. Move!")).mean() > 0.003


def test_gate_triggers_once_then_gone():
    g = ChangeGate(GateParams(stable_frames=2))
    t = 0.0
    actions = []
    for i in range(6):
        actions.append(g.update(_frame("Hello there, Jason!", i % 2), t))
        t += 0.1
    assert actions.count("ocr") == 1
    acts2 = []
    for i in range(5):
        acts2.append(g.update(_frame(None, i), t))
        t += 0.1
    assert "gone" in acts2
    acts3 = [g.update(_frame("A totally new subtitle line", 0), t + k * 0.1) for k in range(4)]
    assert "ocr" in acts3


# --- ayarlar / kısayol / stil --------------------------------------------------------
def test_app_names():
    # ayar ve model klasörleri APP_ID'den türetilir: değişirse kayıtlı ayarlar ve indirilmiş model kaybolur
    from altyazitr import APP_ID, APP_NAME
    from altyazitr.paths import CONFIG_DIR, DATA_DIR

    assert APP_NAME == "Robz Vision" and APP_ID == "RobzVision"
    assert CONFIG_DIR.name == DATA_DIR.name == APP_ID


def test_settings_from_dict_tolerant():
    s = _from_dict(Settings, {"capture": {"fps": "15", "bilinmeyen": 1}, "ocr": {"engine": "rapidocr"},
                              "style": {"font_size": 50.0}, "first_run": 0})
    assert s.capture.fps == 15 and s.ocr.engine == "rapidocr" and s.style.font_size == 50
    assert s.first_run is False and s.translate.engine == "quickmt"


def test_screen_and_stream_styles(tmp_path, monkeypatch):
    """Ekranımda / Yayınımda iki ayrı görünüm; eski tek görünümlü dosya ikisine de aynı başlar."""
    import json

    from altyazitr import config as cfg

    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    path.write_text(json.dumps({"style": {"font_size": 50, "text_color": "#FFD54A"}}), encoding="utf-8")
    s = cfg.load_settings()
    assert s.style.font_size == s.screen_style.font_size == 50
    assert s.screen_style.text_color == "#FFD54A"
    s.screen_style.font_size = 30  # ekrandakini değiştirmek yayını etkilemez (kopya, aynı nesne değil)
    assert s.style.font_size == 50
    cfg.save_settings(s)
    s2 = cfg.load_settings()
    assert (s2.style.font_size, s2.screen_style.font_size) == (50, 30)


def test_default_ocr_engine_and_migration(tmp_path, monkeypatch):
    """Varsayılan RapidOCR; eski dosyadaki (bilerek seçilmemiş) Windows OCR bir kez taşınır, yeni dosyada seçim kalır."""
    import json

    from altyazitr import config as cfg

    assert Settings().ocr.engine == "rapidocr"
    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    path.write_text(json.dumps({"ocr": {"engine": "windows"}}), encoding="utf-8")  # 1.0.2 ve öncesi
    s = cfg.load_settings()
    assert s.ocr.engine == "rapidocr" and s.settings_version == Settings.settings_version
    s.ocr.engine = "windows"  # kullanıcı sonradan bilerek seçerse korunur
    cfg.save_settings(s)
    assert cfg.load_settings().ocr.engine == "windows"


def test_translate_defaults_migration(tmp_path, monkeypatch):
    """1.0.x varsayılanları (TranslateGemma, 3 satır bağlam) bir kez yenilere geçer; kullanıcının seçimi kalır."""
    import json

    from altyazitr import config as cfg

    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    old = {"settings_version": 2, "translate": {"ollama_model": "translategemma:4b", "context_lines": 3}}
    path.write_text(json.dumps(old), encoding="utf-8")
    s = cfg.load_settings()
    assert (s.translate.ollama_model, s.translate.context_lines) == ("gemma4:12b", 8)
    assert s.translate.profile == "gta6" and s.settings_version == 3
    mine = {"settings_version": 2, "translate": {"ollama_model": "qwen3.5:9b", "context_lines": 5}}
    path.write_text(json.dumps(mine), encoding="utf-8")
    s = cfg.load_settings()
    assert (s.translate.ollama_model, s.translate.context_lines) == ("qwen3.5:9b", 5)
    # Ollama'yı TranslateGemma ile kullanan: çalışan kurulumu bozulmaz (Gemma 4 inene kadar hata alırdı)
    using = {"settings_version": 2, "translate": {"engine": "ollama", "ollama_model": "translategemma:4b"}}
    path.write_text(json.dumps(using), encoding="utf-8")
    assert cfg.load_settings().translate.ollama_model == "translategemma:4b"
    # yeni biçimdeki dosyada 3 satır kullanıcının kendi seçimidir
    path.write_text(json.dumps({"settings_version": 3, "translate": {"context_lines": 3}}), encoding="utf-8")
    assert cfg.load_settings().translate.context_lines == 3


def test_english_mass_ignores_background_words():
    # arka plandaki plaka/tabela yazısı ham okumayı öne geçirmesin
    assert english_mass("LAMARG was getting us paid.") == english_mass("was getting us paid.")
    assert english_mass("I was getting us paid.") > english_mass("LAMARG was getting us paid.")


def test_rapidocr_uses_bundled_models():
    pytest.importorskip("rapidocr")
    from altyazitr.ocr import _rapidocr_model_dir

    d = _rapidocr_model_dir()
    assert (d / "PP-OCRv6_det_small.onnx").is_file() and (d / "PP-OCRv6_rec_small.onnx").is_file()


def test_parse_hotkey():
    assert parse_hotkey("Ctrl+Alt+T") == (0x2 | 0x1, 0x54)
    assert parse_hotkey("Shift+F9") == (0x4, 0x78)
    assert parse_hotkey("Ctrl++") == (0x2, 0xBB)
    assert parse_hotkey("Hyper+T") is None
    assert parse_hotkey("") is None


def test_style_vars():
    v = style_vars(Settings().style)
    assert v["size"].endswith("vh") and v["bg"].startswith("rgba(")
    assert v["orig-display"] == "none"


@pytest.mark.parametrize("opts", [
    {},  # varsayılan (yarı saydam kutu + kenar + gölge)
    {"bg_opacity": 0.0},  # arka plan yok
    {"outline_width": 0.0, "shadow": False, "bg_opacity": 0.9},  # kutulu
    {"font_family": "Arial", "font_size": 90, "font_weight": 900, "show_original": True},
])
def test_render_subtitle_variants(opts):
    import dataclasses

    from PySide6.QtGui import QGuiApplication, QImage, QPainter, Qt

    from altyazitr.config import StyleCfg
    from altyazitr.render import paint_subtitle

    _app = QGuiApplication.instance() or QGuiApplication([])
    st = dataclasses.replace(StyleCfg(), **opts)
    img = QImage(640, 360, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    paint_subtitle(p, 640, 360, st, "Arabaya bin, Vice City'ye gidiyoruz!", "LUCIA", "Get in the car!")
    p.end()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(360, img.bytesPerLine())[:, :640 * 4]
    alpha = arr.reshape(360, 640, 4)[:, :, 3]
    assert alpha[:180].max() == 0  # üst yarı boş (altyazı altta)
    assert (alpha[180:] > 0).mean() > 0.01  # alt yarıda yazı var


def test_timing_perf_text_mask():
    img = _frame("Performance check line")
    t = time.perf_counter()
    for _ in range(50):
        text_mask(img)
    assert (time.perf_counter() - t) / 50 < 0.01  # kare başına < 10 ms
