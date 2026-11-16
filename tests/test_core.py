"""Model/ekran gerektirmeyen birim testleri:  python -m pytest -q"""

import time

import cv2
import numpy as np
import pytest

from altyazitr.config import Settings, _from_dict
from altyazitr.detect import ChangeGate, GateParams, text_mask
from altyazitr.english import english_score
from altyazitr.hotkeys import parse_hotkey
from altyazitr.ocr import OcrLine
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
    prot, mapping = g.protect("The cop drove to vice city.", keep_only=False)
    assert "cop" not in prot.lower() and "vice city" not in prot.lower()
    restored = Glossary.restore(prot, mapping)
    assert restored == "The polis drove to Vice City."
    # varsayılan: yalnızca çevrilmeyecek adlar korunur, 'cop' NMT'ye olduğu gibi gider
    prot2, mapping2 = g.protect("The cop drove to vice city.")
    assert "cop" in prot2 and list(mapping2.values()) == ["Vice City"]
    # model boşluk ekleyip küçük harf yapsa da geri yüklenmeli
    tok = next(k for k, v in mapping.items() if v == "Vice City")
    assert Glossary.restore(f"{tok.lower()[:2]} {tok[2:]}'e gittik", {tok: "Vice City"}) == "Vice City'e gittik"
    assert "Vice City = Vice City (do not translate)" in g.prompt_lines()


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
def test_settings_from_dict_tolerant():
    s = _from_dict(Settings, {"capture": {"fps": "15", "bilinmeyen": 1}, "ocr": {"engine": "rapidocr"},
                              "style": {"font_size": 50.0}, "first_run": 0})
    assert s.capture.fps == 15 and s.ocr.engine == "rapidocr" and s.style.font_size == 50
    assert s.first_run is False and s.translate.engine == "quickmt"


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
