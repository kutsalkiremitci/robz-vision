"""Vurgulu (renkli) kelimeler: görev yazısında oyunun renkli yazdığı kelimelerin bulunması, çevirmene [[...]]
işaretleriyle taşınması ve iki çıktıda (ekran kutusu, OBS sayfası) vurgu rengiyle çizilmesi.

Ekran ve ağ gerektirmez. Görüntüler sentetiktir: koyu ya da renkli ışıklı zemine OpenCV ile yazı, video gibi
JPEG (4:2:0) sıkıştırmasıyla. Çeviri motorları ve Ollama/Gemini sahtedir. Qt 'offscreen' QApplication'dan hemen
önce ayarlanır (bkz. test_style_profiles). OBS sayfasının betiği Node.js varsa küçük bir sahte DOM'da çalıştırılır.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import shutil
import subprocess
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from altyazitr import translate as tr_mod
from altyazitr.config import Settings, StyleCfg
from altyazitr.ocr import OcrLine, OcrWord
from altyazitr.paths import resource_dir
from altyazitr.pipeline import Pipeline, TrContext
from altyazitr.server import OverlayServer, clean_spans, style_vars
from altyazitr.textproc import (align_spans, assemble, clean_marks, highlight_spans, line_colors, mark,
                                match_initial_case, parse_marks, unmark)
from altyazitr.translate import (LLM_MARK_RULE, GeminiTranslator, Glossary, OllamaTranslator, QuickMtTranslator,
                                 Translator, build_user_prompt, locate_phrases)

FONT = cv2.FONT_HERSHEY_DUPLEX
# RGB
WHITE, PINK, BLUE, YELLOW = (232, 232, 232), (0xEC, 0x8D, 0xC0), (93, 182, 229), (240, 200, 80)


# --- sentetik görev yazısı ------------------------------------------------------------------------------------------
def _neon(seed: int, h: int = 110, w: int = 900) -> np.ndarray:
    """Gece şehri: koyu zeminde renkli ışık lekeleri (yazının arkasında da)."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 40, np.uint8)
    for _ in range(16):
        c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.circle(img, c, int(rng.integers(8, 30)), [int(v) for v in rng.integers(60, 255, 3)], -1)
    return cv2.GaussianBlur(img, (0, 0), 3)


def _mission(parts, bg=None, outline=False, shadow=True, org=(40, 70), quality=70) -> tuple[np.ndarray, OcrLine]:
    """Görev yazısı: parçalar (metin, RGB) yan yana; kenarsız, hafif gölgeli (GTA 6 gibi) ya da kenarlı. Kare JPEG
    ile sıkıştırılır (renk 4:2:0, video gibi). Döner: kare ve satırın OCR'daki gibi kutusu, kelime kutularıyla."""
    scale, thick = 1.3, 3
    img = np.full((110, 900, 3), 28, np.uint8) if bg is None else bg.copy()
    x, y = org
    union = np.zeros(img.shape[:2], np.uint8)
    layers, words = [], []
    (_, th), base = cv2.getTextSize("Ag", FONT, scale, thick)
    for text, rgb in parts:
        m = np.zeros(img.shape[:2], np.uint8)
        cv2.putText(m, text, (x, y), FONT, scale, 255, thick, cv2.LINE_AA)
        layers.append((m, rgb))
        union = np.maximum(union, m)
        for w in re.finditer(r"\S+", text):
            wx = x + (cv2.getTextSize(text[:w.start()], FONT, scale, thick)[0][0] if w.start() else 0)
            ww = cv2.getTextSize(w.group(), FONT, scale, thick)[0][0]
            words.append(OcrWord(w.group(), wx, y - th - 2, ww, th + base + 4))
        x += cv2.getTextSize(text, FONT, scale, thick)[0][0]
    f = img.astype(np.float32)
    if shadow:
        a = cv2.GaussianBlur(np.roll(union, (2, 2), (0, 1)).astype(np.float32), (0, 0), 1.5) / 255 * 0.8
        f *= 1 - a[..., None]
    if outline:
        f[cv2.dilate((union > 60).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = 0
    for m, rgb in layers:
        a = (m.astype(np.float32) / 255)[..., None]
        f = f * (1 - a) + np.array(rgb[::-1], np.float32) * a
    img = np.clip(f, 0, 255).astype(np.uint8)
    if quality:
        img = cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1], cv2.IMREAD_COLOR)
    ys, xs = np.nonzero(union > 60)
    text = " ".join("".join(t for t, _ in parts).split())
    line = OcrLine(text, 0.99, float(xs.min() - 2), float(ys.min() - 2), float(xs.max() - xs.min() + 4),
                   float(ys.max() - ys.min() + 4), tuple(words))
    return img, line


def _marked(img: np.ndarray, line: OcrLine, word_boxes: bool = True) -> str:
    """Satırın vurgularıyla işaretli metni; word_boxes False ise kelime kutusu vermeyen motor gibi."""
    if not word_boxes:
        line = dataclasses.replace(line, words=())
    return mark(line.text, highlight_spans(img, [line], line.text))


# --- algılama ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("word_boxes", [True, False], ids=["kelime kutulu", "kutusuz"])
def test_pembe_vurgu_bulunur(word_boxes):
    """GTA 6: beyaz "Drive to the" + pembe "radar station" → yalnız pembe kelimeler vurgu."""
    img, line = _mission([("Drive to the ", WHITE), ("radar station", PINK)])
    assert highlight_spans(img, [line], line.text) == [(13, 26)]
    assert _marked(img, line, word_boxes) == "Drive to the [[radar station]]"


@pytest.mark.parametrize("word_boxes", [True, False], ids=["kelime kutulu", "kutusuz"])
def test_mavi_ve_sari_vurgu_da_bulunur(word_boxes):
    """Algılama renkten bağımsız: eski GTA'lardaki mavi kişi ve sarı yer adları da vurgu; noktalama dışarıda."""
    img, line = _mission([("Follow ", WHITE), ("Jason", BLUE), (" to the ", WHITE), ("safehouse", YELLOW),
                          (".", WHITE)])
    assert _marked(img, line, word_boxes) == "Follow [[Jason]] to the [[safehouse]]."


def test_duz_beyaz_satirda_vurgu_yok():
    img, line = _mission([("Hold on tight, we are almost there!", WHITE)])
    assert [f for _, f in line_colors(img, line)] == [False] * 7
    assert highlight_spans(img, [line], line.text) == []


@pytest.mark.parametrize("outline", [True, False], ids=["kenarlı", "gölgeli"])
def test_kenar_golge_ve_renkli_zemin_yanlis_vurgu_uretmez(outline):
    """Renkli ışıklı zeminde kenarlı ya da gölgeli beyaz yazı: zemin rengi harf kenarlarına sızsa da vurgu yok.
    Aynı zeminde pembe kelime yine bulunur."""
    img, line = _mission([("Hold on tight, we are almost there!", WHITE)], bg=_neon(1), outline=outline)
    assert highlight_spans(img, [line], line.text) == []
    img, line = _mission([("Lose the ", WHITE), ("cops", PINK), (".", WHITE)], bg=_neon(3), outline=outline)
    assert _marked(img, line) == "Lose the [[cops]]."


def _stream(img: np.ndarray) -> np.ndarray:
    """720p yayın görüntüsü 1080p'ye büyütülmüş gibi: küçült, 4:2:0 renk alt örneklemesi, büyüt."""
    h, w = img.shape[:2]
    k = cv2.resize(img, (w * 2 // 3 // 2 * 2, h * 2 // 3 // 2 * 2), interpolation=cv2.INTER_AREA)
    k = cv2.cvtColor(cv2.cvtColor(k, cv2.COLOR_BGR2YUV_I420), cv2.COLOR_YUV2BGR_I420)
    return cv2.resize(k, (w, h), interpolation=cv2.INTER_LINEAR)


def test_renkli_zeminden_bulasan_renk_vurgu_sayilmaz():
    """Doygun pembe / turkuaz zeminin önünde gölgeli beyaz yazı, yayın görüntüsü: renk alt örneklemesi zeminin rengini
    ince harflere bulaştırır; bu renk vurgu sayılmaz (önceki sürüm "I", "now" gibi kelimeleri vurguluyordu). Aynı
    zeminde gerçekten renkli (sarı) kelime bulunur. Zeminle aynı tondaki vurgu ise bulunmaz: rengi zeminden
    ayırt edilemez (yanlış alarmı önlemenin bedeli)."""
    bg = np.zeros((110, 900, 3), np.uint8)
    bg[:, :450] = (200, 40, 230)
    bg[:, 450:] = (230, 200, 30)
    img, line = _mission([("Keep the engine running, I will be right back.", WHITE)], bg=bg, quality=0)
    img = _stream(img)
    assert True not in [f for _, f in line_colors(img, line)]
    assert highlight_spans(img, [line], line.text) == []
    img, line = _mission([("Drive to the ", WHITE), ("safehouse", YELLOW), (" now.", WHITE)], bg=bg, quality=0)
    assert _marked(_stream(img), line) == "Drive to the [[safehouse]] now."
    img, line = _mission([("Lose the ", WHITE), ("cops", PINK), (" and hide.", WHITE)], bg=bg, quality=0)
    assert _marked(_stream(img), line) == "Lose the cops and hide."


def test_tek_harfli_kelime_iki_vurgu_arasinda_koprulenir():
    """"Pay 'n' Spray": tek harflik 'n' rengi ölçülemeyecek kadar küçüktür; iki vurgulu kelimenin arasındaysa vurguya
    katılır, gövde kelimesi sayılmaz (yalnız o beyazsa satır tamamı renkli sayılır: vurgu yok)."""
    text = "Take the Phoenix to the Pay 'n' Spray"
    tokens = [(w, False) for w in text.split()[:5]] + [("Pay", True), ("'n'", False), ("Spray", True)]
    assert [text[s:e] for s, e in align_spans(tokens, text)] == ["Pay 'n' Spray"]
    assert align_spans([("Pay", True), ("'n'", False), ("Spray", True)], "Pay 'n' Spray") == []


def test_tamami_renkli_altyazi_vurgu_sayilmaz():
    """Altyazının kendisi sarıysa (beyaz/gri gövde metni yok) hiçbir kelime vurgu değildir."""
    img, line = _mission([("Get in the car, we are leaving now.", YELLOW)], outline=True)
    assert all(f is True for _, f in line_colors(img, line))
    assert highlight_spans(img, [line], line.text) == []


def test_iki_satir_ve_konusmaci_adi():
    """Konuşmacı adı renkli olsa da vurgu sayılmaz (assemble onu ayırır); vurgu ikinci satırda da bulunur."""
    img1, l1 = _mission([("LUCIA:", YELLOW), (" Meet me at the", WHITE)], org=(40, 45), quality=0)
    img2, l2 = _mission([("Bean Machine", PINK), (" tonight.", WHITE)], bg=img1, org=(40, 95))
    text, speaker, _ = assemble([l1, l2])
    assert (text, speaker) == ("Meet me at the Bean Machine tonight.", "LUCIA")
    assert mark(text, highlight_spans(img2, [l1, l2], text)) == "Meet me at the [[Bean Machine]] tonight."


def test_koseli_ayracli_metinde_vurgu_aranmaz():
    img, line = _mission([("Go to ", WHITE), ("[Ammu-Nation]", PINK)])
    assert highlight_spans(img, [line], line.text) == []  # işaretlerle karışırdı


def test_algilama_ucuz():
    """Okunan her yeni satırda çalışır (saniyede 5-10 okuma): iki satırlık karede birkaç ms."""
    img1, l1 = _mission([("Pick up ", WHITE), ("Lucia", BLUE), (" and drive to the", WHITE)], org=(40, 45), quality=0)
    img, l2 = _mission([("radar station", PINK), (" before midnight.", WHITE)], bg=img1, org=(40, 95))
    text = f"{l1.text} {l2.text}"
    highlight_spans(img, [l1, l2], text)
    t = time.perf_counter()
    for _ in range(20):
        spans = highlight_spans(img, [l1, l2], text)
    assert (time.perf_counter() - t) / 20 < 0.010
    assert mark(text, spans) == "Pick up [[Lucia]] and drive to the [[radar station]] before midnight."


def test_gercek_ocr_ile_uctan_uca():
    """Gerçek RapidOCR (kelime kutularıyla, otomatik ön işlemeyle) + assemble + algılama."""
    pytest.importorskip("rapidocr")
    from altyazitr.ocr import RapidOcrEngine

    img, _ = _mission([("Drive to the ", WHITE), ("radar station", PINK)])
    lines = RapidOcrEngine("1", "auto").recognize(img)
    assert lines and lines[0].words  # kelime kutuları geliyor
    text, _, _ = assemble(lines)
    assert mark(text, highlight_spans(img, lines, text)) == "Drive to the [[radar station]]"


# --- metindeki yeri ------------------------------------------------------------------------------------------------
def test_hizalama_ocr_duzeltmesi_ve_satir_sonu_tiresi():
    """Kelimeler sırayla hizalanır: OCR düzeltmesi harf değiştirse, satır sonu tiresi kelimeleri birleştirse de."""
    tokens = [("Go", False), ("to", False), ("Ammu-Natlon", True), ("and", False), ("re-", True), ("treat", True)]
    assert align_spans(tokens, "Go to Ammu-Nation and retreat") == [(6, 17), (22, 29)]
    # konuşmacı adı ve başka bloklar metinde yok: yok sayılır
    assert align_spans([("HUD", True), ("JASON:", True), ("Lose", False), ("the", False), ("cops.", True)],
                       "Lose the cops.") == [(9, 13)]
    # vurgu arasındaki belirsiz kısa kelime köprülenir ("Bank of Liberty" tek ad)
    assert align_spans([("Rob", False), ("the", False), ("Bank", True), ("of", None), ("Liberty", True)],
                       "Rob the Bank of Liberty") == [(8, 23)]


# --- işaretler -------------------------------------------------------------------------------------------------------
def test_isaretler_gidip_gelir():
    s = mark("Drive to the radar station", [(13, 26)])
    assert s == "Drive to the [[radar station]]"
    assert unmark(s) == ("Drive to the radar station", [(13, 26)])
    assert parse_marks("Git [[a [[b]] c]]") is None and parse_marks("[[açık kaldı") is None


@pytest.mark.parametrize("raw", [
    "Radar istasyonuna sür.",  # eksik
    "[[Radar]] [[istasyonuna]] sür.",  # fazla
    "[[Radar [[istasyonuna]]]] sür.",  # iç içe
    "[[Radar istasyonuna sür.",  # kapanmamış
    "[Radar istasyonuna] sür.",  # tek ayraç
    "[[ ]] Radar istasyonuna sür.",  # boş
    "Radar istasyonuna sür [[.]]",  # yalnız noktalama
])
def test_bozuk_isaretler_duz_metne_duser(raw):
    out = clean_marks(raw, expected=1)
    assert "[" not in out and "]" not in out
    assert out.startswith("Radar istasyonuna sür")


@pytest.mark.parametrize("raw, out", [
    ("[[Radar istasyonuna]] sür.", "[[Radar istasyonuna]] sür."),
    ("[[Lucia]]'yı al.", "[[Lucia'yı]] al."),  # ek de vurgulanır: kelime yarım kalmaz
    ("[[Vice City]]'deki [[Bean Machine]]'de buluş.", "[[Vice City'deki]] [[Bean Machine'de]] buluş."),
    ("[[ Polisleri.]] atlat!", "[[Polisleri]]. atlat!"),  # boşluk ve noktalama dışarıda
])
def test_isaretler_kelimeye_oturur(raw, out):
    assert clean_marks(raw, expected=raw.count("[[")) == out


@pytest.mark.parametrize("src, out, want", [
    ("Drive to the radar station.", "[[radar istasyonuna]] sür.", "[[Radar istasyonuna]] sür."),
    ("Go in.", "[[içeri]] gir.", "[[İçeri]] gir."),  # Türkçe i → İ
    ("Go.", "«[[ıslak]] yol»", "«[[Islak]] yol»"),
    ("and then?", "[[sonra]]?", "[[sonra]]?"),  # kaynak küçük harfle başlıyor: dokunulmaz
    ("Go to 5th.", "5. caddeye git", "5. caddeye git"),
])
def test_cumle_basi_buyuk_harf_korunur(src, out, want):
    """Küçük model cümle başı işaretliyken ilk harfi küçük yazabiliyor; kaynak büyük harfle başlıyorsa düzelir."""
    assert match_initial_case(src, out) == want


# --- LLM istemi ------------------------------------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload, self.status_code, self.text = payload, 200, ""

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        pass


class _FakeSession:
    """İstekleri kaydeder; yanıtlar sırayla verilir (son yanıt tekrarlanır)."""

    def __init__(self, *payloads: dict):
        self.payloads, self.calls = list(payloads), []

    def post(self, url, json=None, timeout=None, headers=None):
        self.calls.append(json)
        return _FakeResponse(self.payloads.pop(0) if len(self.payloads) > 1 else self.payloads[0])

    def close(self):
        pass


def _ollama(answer: str, model: str = "gemma4:12b") -> OllamaTranslator:
    t = OllamaTranslator("http://x", model, Glossary())
    t._s = _FakeSession({"message": {"content": answer}})
    return t


def test_isaret_yokken_istem_degismez():
    ctx = [("You good?", "İyi misin?")]
    assert build_user_prompt("Not here.", ctx) == ("Previous lines (context only):\nEN: You good?\nTR: İyi misin?\n"
                                                    "Line to translate:\nEN: Not here.\nTR:")
    assert LLM_MARK_RULE not in build_user_prompt("Go to [Ammu-Nation] now.")  # tek ayraç işaret değil


def test_isaret_varken_kural_eklenir_sistem_istemi_ayni():
    """Vurgulu satır: sistem istemi aynı kalır (Ollama önbelleği), kural kullanıcı mesajında satırdan hemen önce;
    bağlam işaretsiz gider; model işareti taşırsa çıktı işaretli döner."""
    t = _ollama("[[Radar istasyonuna]] sür.")
    ctx = [("Get in.", "Bin.")]
    assert t.translate_marked("Drive to the [[radar station]].", ctx) == "[[Radar istasyonuna]] sür."
    t.translate("Drive to the radar station.", ctx)
    marked, plain = t._s.calls
    assert marked["messages"][0] == plain["messages"][0] == {"role": "system", "content": t.system}
    assert marked["options"] == plain["options"]
    user = marked["messages"][1]["content"]
    assert user.endswith(f"Line to translate:\n{LLM_MARK_RULE}\nEN: Drive to the [[radar station]].\nTR:")
    context_part = user.split("Line to translate:")[0]
    assert "EN: Get in.\nTR: Bin." in context_part and "[" not in context_part
    assert LLM_MARK_RULE not in plain["messages"][1]["content"]


@pytest.mark.parametrize("answer", ["Radar istasyonuna sür.", "[[Radar]] [[istasyonuna]] sür.",
                                    "[[Radar istasyonuna sür."])
def test_llm_isareti_bozarsa_vurgusuz_duz_metin(answer):
    out = _ollama(answer).translate_marked("Drive to the [[radar station]].")
    assert out == "Radar istasyonuna sür."


def test_gemini_ayni_istemi_kullanir():
    t = GeminiTranslator("anahtar", "gemini-3.5-flash-lite", Glossary())
    t._s = _FakeSession({"candidates": [{"content": {"parts": [{"text": "[[Lucia]]'yı al."}]}}]})
    assert t.translate_marked("Pick up [[Lucia]].") == "[[Lucia'yı]] al."
    body = t._s.calls[0]
    assert body["systemInstruction"]["parts"][0]["text"] == t.system
    assert body["contents"][0]["parts"][0]["text"] == build_user_prompt("Pick up [[Lucia]].")


def test_translategemma_ve_deepl_vurgusuz_cevirir():
    t = _ollama("Lucia'yı al.", model="translategemma:12b")
    assert t.translate_marked("Pick up [[Lucia]].") == "Lucia'yı al."
    assert "[[" not in json.dumps(t._s.calls[0], ensure_ascii=False)
    deepl = tr_mod.DeepLTranslator("anahtar:fx", Glossary())
    deepl._s = _FakeSession({"translations": [{"text": "Lucia'yı al."}]})
    assert deepl.translate_marked("Pick up [[Lucia]].") == "Lucia'yı al."
    assert deepl._s.calls[0]["text"] == ["Pick up Lucia."]


# --- quickmt: ifadeyi ayrıca çevirip tam çeviride bulma --------------------------------------------------------------
@pytest.mark.parametrize("full, phrase, found", [
    ("Radar istasyonuna sür.", "radar istasyonu", "Radar istasyonuna"),  # ek
    ("Polisleri atlat.", "polisler", "Polisleri"),
    ("Lucia'yı al.", "Lucia", "Lucia'yı"),  # kesme işaretli ek
    ("Kitabı getir.", "kitap", "Kitabı"),  # kök sonu yumuşar
    ("İSTASYONA GİT.", "istasyon", "İSTASYONA"),  # Türkçe büyük İ
    ("Kamyonu takip et.", "araba", None),  # eşleşmez: vurgusuz
    ("Ev sahibini ara.", "ev", "Ev"),  # tek kelimelik ifadede kısa kelime yalnız tam eşleşir
    ("Evet, haklısın.", "ev", None),
    ("Jason'ı güvenli eve götür.", "Güvenli ev.", "güvenli eve"),  # çok kelimelide kısa kelimenin eki de olur
    ("Karakola git.", "Polis karakolu.", "Karakola"),  # tamlama tek kelimeyle söylenmiş: asıl kelime sonda
])
def test_quickmt_ifadeyi_ekli_haliyle_bulur(full, phrase, found):
    spans = locate_phrases(full, [phrase])
    assert [full[s:e] for s, e in spans] == ([found] if found else [])


class _FakeCt2:
    """quickmt'nin CTranslate2 ve SentencePiece nesnelerinin yerine: sözlükten çevirir, çağrıları sayar."""

    def __init__(self, table: dict[str, str]):
        self.table, self.batches = table, []

    def encode(self, text, out_type=str):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)

    def translate_batch(self, batch, **_kw):
        self.batches.append(batch)
        return [SimpleNamespace(hypotheses=[self.table[" ".join(toks[:-1])].split()]) for toks in batch]


def _quickmt(table: dict[str, str], glossary: Glossary | None = None) -> tuple[QuickMtTranslator, _FakeCt2]:
    q = QuickMtTranslator(glossary or Glossary())
    fake = _FakeCt2(table)
    q._tr = q._src = q._tgt = fake
    return q, fake


def test_quickmt_model_survives_restarts(monkeypatch, tmp_path):
    """İşlem hattı her yeniden başlatmada yeni bir QuickMtTranslator kurar; model diskten bir kez yüklenir,
    sonrakiler bellektekini kullanır (yeniden başlatmada ~1 sn altyazısız kalınmasın). close() modeli atmaz."""
    from altyazitr import translate as tr_mod

    loads: list[str] = []

    class FakeCtranslate2:
        @staticmethod
        def Translator(path, device="cpu", **_kw):  # noqa: N802 - ctranslate2 adı
            loads.append(device)
            return _FakeCt2({})

    protos: list[bytes] = []

    class FakeSpm:
        @staticmethod
        def SentencePieceProcessor(*, model_proto):  # noqa: N802 - sentencepiece adı
            protos.append(model_proto)
            return _FakeCt2({})

    d = tmp_path / "ömer" / "quickmt-en-tr"  # Türkçe harfli kullanıcı klasörü: model baytlardan yüklenir
    d.mkdir(parents=True)
    (d / "src.spm.model").write_bytes(b"src")
    (d / "tgt.spm.model").write_bytes(b"tgt")
    monkeypatch.setitem(__import__("sys").modules, "ctranslate2", FakeCtranslate2)
    monkeypatch.setitem(__import__("sys").modules, "sentencepiece", FakeSpm)
    monkeypatch.setattr(tr_mod, "QUICKMT_DIR", d)
    monkeypatch.setattr(tr_mod, "quickmt_downloaded", lambda: True)
    monkeypatch.setattr(tr_mod, "_quickmt_models", {})
    first = QuickMtTranslator(Glossary())
    first.prepare(lambda _m: None)
    first.close()
    again = QuickMtTranslator(Glossary())
    again.prepare(lambda _m: None)
    assert loads == ["cpu"] and again._tr is not None
    assert protos == [b"src", b"tgt"]


def test_sentencepiece_modeli_turkce_harfli_yoldan_yuklenir(tmp_path):
    """Gerçek SentencePiece: C:\\Users\\ömer gibi ASCII dışı harfli klasördeki model yüklenir ve çalışır (kütüphane
    böyle bir yolu kendisi açamıyor; kullanıcıda "Çeviri motoru hazırlanamadı: NOT_FOUND" hatası)."""
    import io

    spm = pytest.importorskip("sentencepiece")
    buf = io.BytesIO()
    text = ["We need to get out of here.", "Get in the car, we are leaving now.", "Nobody is following us."] * 20
    spm.SentencePieceTrainer.train(sentence_iterator=iter(text), model_writer=buf, vocab_size=40,
                                   hard_vocab_limit=False, minloglevel=2)
    d = tmp_path / "ömer şğı" / "quickmt-en-tr"
    d.mkdir(parents=True)
    (d / "src.spm.model").write_bytes(buf.getvalue())
    from altyazitr.translate import _load_spm

    sp = _load_spm(str(d / "src.spm.model"))
    assert sp.decode(sp.encode("Get in the car.")) == "Get in the car."


def test_quickmt_vurguyu_tek_toplu_cagriyla_tasir():
    """Vurgulu ifade tam satırla aynı toplu çağrıda, "The … ." kalıbında çevrilir; çevrilmeyecek ad (Jason) modele
    hiç gitmez. Bulunamayan ifade vurgusuz kalır, bulunan kalır."""
    q, fake = _quickmt({"Drive to the radar station.": "Radar istasyonuna sür.",
                        "The radar station.": "Radar istasyonu.",
                        "Take ZQX0 to the safehouse.": "ZQX0'ı sığınağa götür.",
                        "The safehouse.": "Güvenli ev."}, Glossary([["Jason", ""]]))
    assert q.translate_marked("Drive to the [[radar station]].") == "[[Radar istasyonuna]] sür."
    assert len(fake.batches) == 1 and len(fake.batches[0]) == 2  # tam satır + ifade tek çağrıda
    assert q.translate_marked("Take [[Jason]] to the [[safehouse]].") == "[[Jason'ı]] sığınağa götür."
    assert [" ".join(b[:-1]) for b in fake.batches[1]] == ["Take ZQX0 to the safehouse.", "The safehouse."]
    assert q.translate("Drive to the radar station.") == "Radar istasyonuna sür."


# --- işlem hattı -----------------------------------------------------------------------------------------------------
class _MarkingTr(Translator):
    """LLM gibi işareti taşıyan sahte motor: çeviri, kaynağın kendisi (işaretleriyle) + önek."""

    def __init__(self, key: str = "llm", answer: str | None = None):
        self.key, self.label = key, f"sahte {key}"
        self.answer = answer
        self.calls: list[tuple[str, list]] = []

    def translate(self, text, context=()):
        self.calls.append((text, list(context)))
        return f"TR {text}"

    def translate_marked(self, text, context=()):
        self.calls.append((text, list(context)))
        return self.answer if self.answer is not None else f"TR {text}"


@pytest.fixture
def pipe(tmp_path, monkeypatch) -> Pipeline:
    from altyazitr import pipeline as pl

    monkeypatch.setattr(pl, "CACHE_FILE", tmp_path / "onbellek.json")
    s = Settings()
    s.translate.engine, s.translate.hybrid = "ollama", False
    return Pipeline(s)


def _say(pipe: Pipeline, cx: TrContext, text: str, spans=(), t: float = 1000.0) -> list[dict]:
    """Okunan satırı (vurgularıyla) çeviri adımından geçirir; arayüze giden olayları döndürür."""
    got: list[dict] = []
    pipe.subtitle.connect(got.append)
    try:
        ev = pipe.tracker.on_text(text, None, t)
        ev["spans"] = list(spans)
        pipe._apply(pipe._translate_item(("sub", t, ev), cx))
    finally:
        pipe.subtitle.disconnect(got.append)
    return got


def test_ocr_okumasi_vurguyu_tasir(pipe):
    """OCR adımı satırın vurgularını okumaya ekler; iki görünümde de vurgu kapalıysa hiç aramaz."""
    img, line = _mission([("Drive to the ", WHITE), ("radar station", PINK)])
    engine = SimpleNamespace(recognize=lambda _img: [line])
    [(kind, _t, ev)] = pipe._ocr_item(engine, ("text", 1000.0, img))
    assert kind == "sub" and ev["text"] == "Drive to the radar station" and ev["spans"] == [(13, 26)]
    pipe.s.style.highlight = pipe.s.screen_style.highlight = False
    pipe.tracker.on_gone(1000.5)
    [(_, _, ev)] = pipe._ocr_item(engine, ("text", 1005.0, img))
    assert ev["spans"] == []


def test_olayda_duz_metin_ve_ayri_vurgu_alani(pipe):
    """Arayüze giden olayda metin düzdür (işaret yok); vurgular Türkçe metindeki aralıklar olarak ayrı alanda.
    Vurgusuz satırın olayı eskisi gibidir (spans alanı yok)."""
    cx = TrContext(pipe.s.translate, _MarkingTr(answer="[[Radar istasyonuna]] sür."), None)
    [ev] = _say(pipe, cx, "Drive to the radar station.", [(13, 26)])
    assert ev["text"] == "Radar istasyonuna sür." and ev["spans"] == [[0, 17]]
    assert ev["original"] == "Drive to the radar station."
    cx.main.answer = None
    [ev2] = _say(pipe, cx, "Get in.", t=1001.0)
    assert ev2["text"] == "TR Get in." and "spans" not in ev2


def test_bagliama_isaretsiz_metin_girer(pipe):
    main = _MarkingTr(answer="[[Radar istasyonuna]] sür.")
    cx = TrContext(pipe.s.translate, main, None)
    _say(pipe, cx, "Drive to the radar station.", [(13, 26)])
    main.answer = None
    _say(pipe, cx, "Hurry up!", t=1001.0)
    assert main.calls[-1] == ("Hurry up!", [("Drive to the radar station.", "Radar istasyonuna sür.")])


def test_bozuk_cikti_ekrana_isaret_gondermez(pipe):
    cx = TrContext(pipe.s.translate, _MarkingTr(answer="[[Radar istasyonuna sür."), None)
    [ev] = _say(pipe, cx, "Drive to the radar station.", [(13, 26)])
    assert ev["text"] == "Radar istasyonuna sür." and "spans" not in ev


def test_kucuk_harfle_baslayan_isaretli_cikti_duzelir(pipe):
    cx = TrContext(pipe.s.translate, _MarkingTr(answer="[[radar istasyonuna]] sür."), None)
    [ev] = _say(pipe, cx, "Drive to the radar station.", [(13, 26)])
    assert ev["text"] == "Radar istasyonuna sür." and ev["spans"] == [[0, 17]]


def test_onbellek_vurgulu_ve_vurgusuz_karismaz(pipe):
    """Aynı metnin vurgulu ve vurgusuz çevirisi ayrı anahtarlarda; bulanık arama da karıştırmaz."""
    main = _MarkingTr()
    cx = TrContext(pipe.s.translate, main, None)
    text = "Meet Cal outside the Bean Machine after the heist tonight, and bring the getaway car."
    spans = [(text.index("Bean Machine"), text.index("Bean Machine") + 12)]
    plain_ev = {"key": "meet cal", "text": text}
    assert cx.cache_key(plain_ev) != cx.cache_key({**plain_ev, "spans": spans})

    [a] = _say(pipe, cx, text)  # vurgusuz: önbelleğe düz
    assert "spans" not in a and not a["cached"]
    pipe.tracker.on_gone(1000.5)
    [b] = _say(pipe, cx, text, spans, t=1005.0)  # aynı metin vurgulu: önbellekten gelmez, işaretli çevrilir
    assert not b["cached"] and b["text"][b["spans"][0][0]:b["spans"][0][1]] == "Bean Machine"
    pipe.tracker.on_gone(1005.5)
    [c] = _say(pipe, cx, text, t=1010.0)  # vurgusuz yine: önbellekten düz
    assert c["cached"] and "spans" not in c and "[[" not in c["text"]
    pipe.tracker.on_gone(1010.5)
    [d] = _say(pipe, cx, text, spans, t=1015.0)  # vurgulu yine: önbellekten vurgulu
    assert d["cached"] and d["spans"] == b["spans"]
    # tek harf farklı okuma (bulanık arama): vurgulu satır vurgusuz çeviriyi almaz, vurgusuz satır işaret almaz
    pipe.cache.clear()
    pipe.cache.put(cx.cache_key({**plain_ev, "key": pipe.tracker.key}), "DÜZ")
    pipe.tracker.on_gone(1015.5)
    other = text.replace("heist", "heisl")
    [e] = _say(pipe, cx, other, spans, t=1020.0)
    assert not e["cached"]
    pipe.cache.clear()
    pipe.cache.put(cx.cache_key({"key": pipe.tracker.key, "text": text, "spans": spans}), "[[Vurgulu]] çeviri")
    pipe.cache.put(cx.cache_key({"key": pipe.tracker.key}), "[[Vurgulu]] çeviri")  # eski bir kayıt
    pipe.tracker.on_gone(1020.5)
    [f] = _say(pipe, cx, text, t=1025.0)
    assert f["cached"] and f["text"] == "Vurgulu çeviri" and "spans" not in f


def test_karma_modda_hizli_ceviri_de_vurgulu(pipe):
    """Karma mod: asıl çeviri gecikirse gösterilen hızlı çeviri de vurgulu, asıl çeviri gelince vurgusuyla günceller."""
    pipe.s.translate.hybrid = True
    pipe.s.translate.hybrid_wait_ms = 0
    cx = TrContext(pipe.s.translate, _MarkingTr(answer="[[Radar istasyonuna]] sür."),
                   _MarkingTr("hızlı", answer="[[Radar istasyonuna]] git."))
    cx.set_ready()
    got: list[dict] = []
    pipe.subtitle.connect(got.append)
    ev = pipe.tracker.on_text("Drive to the radar station.", None, 1000.0)
    ev["spans"] = [(13, 26)]
    pipe._apply(pipe._translate_item(("sub", 1000.0, ev), cx))
    pipe._apply(pipe._remote_item(pipe._remote_q.get(0), cx))
    assert [(g["text"], g["spans"]) for g in got] == [("Radar istasyonuna git.", [[0, 17]]),
                                                     ("Radar istasyonuna sür.", [[0, 17]])]


# --- çizim: ekran kutusu ---------------------------------------------------------------------------------------------
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


_WIDGETS: list = []


@pytest.fixture(autouse=True)
def _close_widgets():
    """Açılan pencereler her testten sonra Qt tarafında silinir (süreç kapanırken çökmesin)."""
    yield
    if not _WIDGETS:
        return
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    for w in _WIDGETS:
        w.close()
        w.deleteLater()
    _WIDGETS.clear()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def _plain_style(**kw) -> StyleCfg:
    """Renkleri ölçülebilir görünüm: kenar, gölge ve kutu yok; yazı beyaz, vurgu saf yeşil."""
    st = StyleCfg(outline_width=0.0, shadow=False, bg_opacity=0.0, text_color="#FFFFFF", highlight_color="#00FF00",
                  font_size=40)
    return dataclasses.replace(st, **kw)


def _paint(st: StyleCfg, text: str, spans=(), W: int = 900, H: int = 300, **kw) -> np.ndarray:
    from PySide6.QtGui import QImage, QPainter

    from altyazitr.render import paint_subtitle

    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    paint_subtitle(p, W, H, st, text, None, "", ref_height=1080, spans=spans, **kw)
    p.end()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(H, img.bytesPerLine())[:, : W * 4]
    return arr.reshape(H, W, 4).copy()


def _where(px: np.ndarray, bgr: tuple[int, int, int]) -> np.ndarray:
    """Rengi verilen renge yakın, görünür pikseller (BGRA)."""
    return (np.abs(px[..., :3].astype(int) - np.array(bgr)).max(axis=2) < 40) & (px[..., 3] > 200)


def test_ekran_kutusu_vurguyu_vurgu_renginde_cizer(qapp):
    """Vurgulu parça vurgu renginde, öncesi ve sonrası yazı renginde (tek satırda, soldan sağa)."""
    text = "Kuzeye sür, Radar istasyonuna gir"
    s = text.index("Radar")
    wide = dict(W=2400, H=200)  # tek satıra sığsın (testte yazı tipi yok: her harf geniş bir kare)
    px = _paint(_plain_style(font_size=30), text, [(s, s + len("Radar istasyonuna"))], **wide)
    green, white = _where(px, (0, 255, 0)), _where(px, (255, 255, 255))
    assert green.sum() > 100 and white.sum() > 100
    gy, gx = np.nonzero(green)
    wy, wx = np.nonzero(white)
    assert abs(np.median(gy) - np.median(wy)) < 3  # aynı satır
    left, right = wx[wx < gx.min()], wx[wx > gx.max()]
    assert left.size and right.size  # "Kuzeye sür," ve "gir" beyaz kalır
    # vurgu kapalıysa ya da vurgu yoksa hiç vurgu renginde piksel yok
    assert not _where(_paint(_plain_style(font_size=30, highlight=False), text, [(s, s + 17)], **wide),
                      (0, 255, 0)).any()
    assert not _where(_paint(_plain_style(font_size=30), text, **wide), (0, 255, 0)).any()


def test_satir_kaydirinca_vurgu_dogru_satirda(qapp):
    """Uzun satır sarılınca vurgu, kelimenin düştüğü alt satırda çizilir."""
    text = "Önce kasabaya git, sonra arabayı değiştir ve en sonunda Radar istasyonuna sür"
    s = text.index("Radar")
    px = _paint(_plain_style(max_width_percent=40.0), text, [(s, s + 17)], H=400)
    green, white = _where(px, (0, 255, 0)), _where(px, (255, 255, 255))
    assert green.any() and np.nonzero(green)[0].min() > np.nonzero(white)[0].min() + 20


def test_ekran_kutusu_satirlarla_vurguyu_tasir(qapp):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage, QRegion
    from PySide6.QtWidgets import QWidget

    from altyazitr.overlay import ScreenOverlay

    s = Settings()
    s.overlay.enabled = False  # kutu gösterilmez; render ile çizilir
    s.screen_style = _plain_style(font_size=30)
    ov = ScreenOverlay(s)
    _WIDGETS.append(ov)
    ov.resize(1500, 300)
    ov.set_lines([{"line": 1, "text": "Eski satır", "original": "a"},
                  {"line": 2, "text": "Radar istasyonuna sür", "original": "b", "spans": [[0, 17]]}])
    assert ov.lines[-1]["spans"] == [[0, 17]] and ov.lines[0]["spans"] == []
    ov._anim.stop()
    ov._shift.stop()
    ov._opacity, ov._progress = 1.0, 1.0
    img = QImage(ov.size(), QImage.Format.Format_ARGB32)
    img.fill(0)
    ov.render(img, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    px = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, : 1500 * 4]
    assert _where(px.reshape(img.height(), 1500, 4), (0, 255, 0)).sum() > 100


# --- iki çıktıya iletim ve OBS ----------------------------------------------------------------------------------------
class _Bridge:
    def submit(self, coro):
        coro.close()


class _Box:
    def __init__(self):
        self.calls: list[list[dict]] = []

    def set_lines(self, lines):
        self.calls.append([dict(x) for x in lines])

    def clear(self):
        self.calls.append([])

    def update(self):
        pass


def test_display_vurguyu_iki_ciktiya_iletir(qapp):
    from altyazitr.display import SubtitleDisplay

    box = _Box()
    d = SubtitleDisplay(Settings(), _Bridge(), OverlayServer(0), box)
    try:
        d.on_subtitle({"line": 1, "text": "Radar istasyonuna sür.", "original": "x", "spans": [[0, 17]]})
        assert box.calls[-1][0]["spans"] == [[0, 17]]
        d.on_subtitle({"line": 1, "text": "Radar istasyonuna sür.", "original": "x"})  # güncelleme vurgusuz
        assert "spans" not in box.calls[-1][0]
        d.on_subtitle({"line": 2, "text": "Kısa.", "original": "y", "spans": [[0, 99], [3, 1]]})  # geçersiz
        assert "spans" not in box.calls[-1][-1]
        d.show_test()  # Test düğmesi: örnek satırda vurgu
        shown = [(x["text"], x.get("spans")) for x in box.calls[-1]]
        assert any(sp and t[sp[0][0]:sp[0][1]] == "Vice City'ye" for t, sp in shown)
        # en yeni örnek: geliştiricinin kanalına davet, kanal adı vurgu renginde (varsayılan pembe)
        assert any(sp and t[sp[0][0]:sp[0][1]] == "Robz so good" and "kick.com/robzsogood" in t for t, sp in shown)
    finally:
        d._hide.stop()


def test_obs_mesajinda_vurgu_alani_ve_stil():
    server = OverlayServer(0)
    asyncio.run(server.set_lines([
        {"line": 1, "text": "Radar istasyonuna sür.", "original": "a", "spans": [[0, 17]]},
        {"line": 2, "text": "Kısa.", "original": "b", "spans": [[0, 99], "x", [3, 1]]},
        {"line": 3, "text": "Düz.", "original": "c"},
    ]))
    lines = server.state["lines"]["lines"]
    assert lines[0]["spans"] == [[0, 17]] and "spans" not in lines[1] and "spans" not in lines[2]
    assert clean_spans([[0, 5], [3, 8], [6, 9]], 10) == [[0, 5], [6, 9]]  # çakışan atılır
    v = style_vars(StyleCfg())
    assert v["hl"] == "#EC8DC0"
    assert style_vars(StyleCfg(highlight=False, text_color="#FFE14D"))["hl"] == "#FFE14D"


def _page() -> str:
    return (resource_dir() / "web" / "overlay.html").read_text(encoding="utf-8")


def test_obs_sayfasi_metni_html_olarak_yazmaz():
    page = _page()
    assert "innerHTML" not in page and "insertAdjacentHTML" not in page and "outerHTML" not in page
    assert ".hl { color: var(--hl); }" in page and "--hl:" in page


# OBS sayfasının betiği için en küçük sahte DOM: innerHTML kullanılırsa hata verir
_DOM_HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
class Text_ {
  constructor(t) { this.nodeType = 3; this.data = String(t); this.parent = null; }
  get textContent() { return this.data; }
  remove() {
    if (this.parent) this.parent.childNodes = this.parent.childNodes.filter((x) => x !== this);
    this.parent = null;
  }
}
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.childNodes = []; this.parent = null; this.className = '';
    this.style = { setProperty(k, v) { this[k] = v; }, removeProperty() {} };
    const self = this;
    const cls = () => new Set(self.className.split(/\s+/).filter(Boolean));
    this.classList = {
      add(...c) { const s = cls(); c.forEach((x) => s.add(x)); self.className = [...s].join(' '); },
      remove(...c) { const s = cls(); c.forEach((x) => s.delete(x)); self.className = [...s].join(' '); },
      contains(c) { return cls().has(c); },
    };
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  appendChild(n) { n.remove(); n.parent = this; this.childNodes.push(n); return n; }
  append(...ns) { for (const n of ns) this.appendChild(typeof n === 'string' ? new Text_(n) : n); }
  remove() { Text_.prototype.remove.call(this); }
  after(n) {
    n.remove();
    const p = this.parent;
    p.childNodes.splice(p.childNodes.indexOf(this) + 1, 0, n);
    n.parent = p;
  }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(''); }
  set textContent(v) {
    this.childNodes.forEach((n) => { n.parent = null; });
    this.childNodes = [];
    if (v !== '') this.append(String(v));
  }
  set innerHTML(_v) { throw new Error('innerHTML kullanıldı'); }
  querySelector(sel) {
    for (const c of this.children) {
      if (c.classList.contains(sel.slice(1))) return c;
      const r = c.querySelector(sel);
      if (r) return r;
    }
    return null;
  }
  getBoundingClientRect() { return { top: 0 }; }
  compareDocumentPosition() { return 0; }
}
const els = { stack: new El('div'), mask: new El('div'), status: new El('div') };
const sockets = [];
const ctx = {
  document: { getElementById: (id) => els[id], createElement: (t) => new El(t), documentElement: new El('html') },
  window: {}, navigator: {}, location: { protocol: 'http:', host: '127.0.0.1:1', search: '' }, URLSearchParams,
  matchMedia: () => ({ matches: true }), getComputedStyle: () => ({ getPropertyValue: () => '0', transform: 'none' }),
  requestAnimationFrame: (f) => f(), setTimeout: () => 0, Node: { DOCUMENT_POSITION_PRECEDING: 2 },
  WebSocket: class { constructor() { sockets.push(this); } },
};
vm.createContext(ctx);
vm.runInContext(script, ctx);
const ws = sockets[0];
ws.onopen();
const dump = (n) => n.nodeType === 3 ? n.data : { tag: n.tagName, cls: n.className, kids: n.childNodes.map(dump) };
const out = [];
for (const m of JSON.parse(process.argv[3])) { ws.onmessage({ data: JSON.stringify(m) }); out.push(dump(els.stack)); }
process.stdout.write(JSON.stringify(out));
"""


def _run_page(messages: list[dict], tmp_path) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js yok: OBS sayfasının betiği çalıştırılamadı")
    harness = tmp_path / "dom.js"
    harness.write_text(_DOM_HARNESS, encoding="utf-8")
    page = resource_dir() / "web" / "overlay.html"
    r = subprocess.run([node, str(harness), str(page), json.dumps(messages)], capture_output=True, text=True,
                       encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _find(node, cls: str) -> list:
    if isinstance(node, str):
        return []
    found = [node] if cls in node["cls"].split() else []
    return found + [x for k in node["kids"] for x in _find(k, cls)]


def _tags(node) -> set[str]:
    return set() if isinstance(node, str) else {node["tag"], *(t for k in node["kids"] for t in _tags(k))}


def test_obs_sayfasi_vurguyu_cizer_ve_metni_kacislar(tmp_path):
    """Sayfa betiği sahte DOM'da: OCR'dan gelen HTML benzeri metin düz metin kalır (öğe oluşmaz); vurgulu parça
    .hl öğesinde; geçersiz aralıklar yok sayılır; aynı satırın güncellemesi vurguyu yeniler."""
    evil = "<img src=x onerror=alert(1)> "
    text = evil + "Radar istasyonuna sür."
    first = {"type": "lines", "lines": [
        {"line": 1, "text": text, "speaker": "<b>X</b>", "original": "<i>o</i>",
         "spans": [[len(evil), len(evil) + 17]]},
        {"line": 2, "text": "Kısa satır.", "speaker": None, "original": "", "spans": [[5, 2], [0, 99], "x"]},
    ]}
    update = {"type": "lines", "lines": [{"line": 1, "text": "Radar istasyonuna sür.", "speaker": None,
                                          "original": "", "spans": [[0, 5]]}]}
    after_first, after_update = _run_page([first, update], tmp_path)
    assert _tags(after_first) == {"DIV", "SPAN"}  # IMG, B, I oluşmadı
    t1, t2 = _find(after_first, "t")
    assert t1["kids"] == [evil, {"tag": "SPAN", "cls": "hl", "kids": ["Radar istasyonuna"]}, " sür."]
    assert _find(after_first, "sp")[0]["kids"] == ["<b>X</b>: "]
    assert t2["kids"] == ["Kısa satır."]
    [kept] = [ln for ln in _find(after_update, "ln") if "off" not in ln["cls"].split()]  # 2. satır çıkıyor
    assert _find(kept, "t")[0]["kids"] == [{"tag": "SPAN", "cls": "hl", "kids": ["Radar"]}, " istasyonuna sür."]


# --- ayarlar ve görünüm sayfası ---------------------------------------------------------------------------------------
def test_eski_ayar_dosyasi_vurgu_varsayilanlariyla_acilir(tmp_path, monkeypatch):
    from altyazitr import config as cfg

    path = tmp_path / "ayarlar.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", path)
    path.write_text(json.dumps({"style": {"font_size": 50}, "screen_style": {"font_size": 30}}), encoding="utf-8")
    s = cfg.load_settings()
    for st in (s.style, s.screen_style):
        assert st.highlight is True and st.highlight_color == "#EC8DC0"


def _style_page(qapp):
    from altyazitr.gui.style_page import StylePage

    s = Settings()
    calls = {"save": 0, "push": 0}

    def bump(key: str) -> None:
        calls[key] += 1

    ctx = SimpleNamespace(s=s, save_later=lambda: bump("save"), overlay=SimpleNamespace(apply_settings=lambda: None),
                          display=SimpleNamespace(push_style=lambda: bump("push"), show_test=lambda: None))
    pg = StylePage(SimpleNamespace(ctx=ctx, toast=lambda *_a: None))
    _WIDGETS.append(pg)
    return pg, s, calls


def test_gorunum_sayfasinda_vurgu_rengi(qapp):
    """Vurgu rengi seçici yalnız seçili görünümü değiştirir; vurgu kapalıyken renk satırı gizlenir; kopyalama ve
    "Varsayılana dön" vurgu ayarlarını da kapsar."""
    pg, s, calls = _style_page(qapp)
    assert pg.side_key == "screen"
    assert pg.highlight_color._buttons[0][0] == "#ec8dc0" and pg.highlight_color._buttons[0][1].isChecked()
    pg.highlight_color._buttons[1][1].click()  # açık mavi
    assert s.screen_style.highlight_color == "#8BE9FD" and s.style.highlight_color == "#EC8DC0"
    assert calls["save"] == calls["push"] == 1
    assert pg._hl_color_row.isVisibleTo(pg)
    pg.highlight.group.button(0).click()  # kapalı
    assert s.screen_style.highlight is False and not pg._hl_color_row.isVisibleTo(pg)
    pg.side.group.button(1).click()  # yayın görünümü: kendi değerleri
    assert pg.highlight.group.checkedId() == 1 and pg._hl_color_row.isVisibleTo(pg)
    pg._copy_btn.click()  # ekrandakini yayına kopyala
    assert (s.style.highlight, s.style.highlight_color) == (False, "#8BE9FD")
    pg._reset_btn.click()
    assert (s.style.highlight, s.style.highlight_color) == (True, "#EC8DC0")
    assert s.screen_style.highlight is False  # öbür görünüm değişmez


def test_gecmiste_isaret_gorunmez(qapp):
    """Son çeviriler listesi olaydaki düz metni gösterir; vurgu alanını yok sayar."""
    from altyazitr.gui.history import HistoryModel, entry_text

    m = HistoryModel()
    m.add({"line": 1, "text": "Radar istasyonuna sür.", "original": "Drive to the radar station.",
           "spans": [[0, 17]]})
    assert entry_text(m.entry(0), original=True) == "Drive to the radar station.\nRadar istasyonuna sür."
