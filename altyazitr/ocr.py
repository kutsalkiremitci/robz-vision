"""OCR motorları.

* RapidOcrEngine — PP-OCRv6 (ONNX, CPU; modeller rapidocr paketiyle gelir). Varsayılan ve önerilen motor. Modeller
  doğrudan onnxruntime ile, sınırlı iş parçacığıyla çalışır (bkz. PpOcr): tek satırlık okuma ~40-75 ms.
* WindowsOcrEngine — Windows'un yerleşik OCR'ı (Windows.Media.Ocr). İngilizce OCR dil paketi gerekir, ~10 ms.

Her motor yalnızca *tek bir* iş parçacığında (OCR iş parçacığı) oluşturulup kullanılmalıdır.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from altyazitr.detect import outlined_text_mask, text_mask
from altyazitr.english import english_mass, english_score
from altyazitr.paths import MODEL_DIR

log = logging.getLogger(__name__)


@dataclass
class OcrWord:
    """Satırdaki bir kelimenin kutusu (motor veriyorsa), kaynak görüntünün koordinatında."""

    text: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class OcrLine:
    text: str
    conf: float | None
    x: float
    y: float
    w: float
    h: float
    # Kelime kutuları, soldan sağa. Vurgulu (renkli) kelimelerin algılanması kullanır; motor vermezse boş kalır ve
    # kelimelerin yeri harflerin arasındaki boşluklardan bulunur (bkz. detect.word_colors).
    words: tuple[OcrWord, ...] = ()


class OcrUnavailable(RuntimeError):
    """Motor bu sistemde kullanılamıyor (kullanıcıya gösterilecek Türkçe mesaj içerir)."""


def preprocess(bgr: np.ndarray, upscale: str = "auto", mode: str = "none") -> tuple[np.ndarray, float]:
    h = bgr.shape[0]
    if upscale == "auto":
        f = 2.0 if h < 100 else (1.5 if h < 150 else 1.0)
    else:
        try:
            f = float(upscale)
        except ValueError:
            f = 1.0
    img = bgr
    if f != 1.0:
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    if mode == "bright":
        # parlak (beyaz/sarı) metni ayır -> beyaz zemin üzerinde siyah yazı
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, (0, 0, 185), (180, 60, 255))
        yellow = cv2.inRange(hsv, (15, 80, 150), (40, 255, 255))
        img = _ink_on_paper(cv2.morphologyEx(white | yellow, cv2.MORPH_CLOSE, np.ones((2, 2), np.uint8)))
    elif mode == "outline":
        # her renkte kenarlı yazı (mavi, kırmızı, yeşil vurgular dahil)
        img = _ink_on_paper(outlined_text_mask(img, min_v=OUTLINE_ANY_V))
    elif mode == "outline_bright":
        # yalnız çok parlak kenarlı yazı: kumlu, açık tonlu zeminde (asfalt) harflere doku yapışmaz
        img = _ink_on_paper(outlined_text_mask(img, min_v=OUTLINE_BRIGHT_V))
    return img, f


# Tek eşik ikisine birden yetmiyor: düşük eşik renkli yazıyı tam alır ama açık asfaltı harflere yapıştırır,
# yüksek eşik asfaltta temizdir ama koyu tonlu renkleri böler. Otomatik mod ikisini de okuyup seçer.
OUTLINE_ANY_V = 160
OUTLINE_BRIGHT_V = 200

# Maskeli ön işleme kiplerinde sayfanın her kenarına eklenen boşluk (px). OCR kutuları bu sayfanın koordinatında
# gelir; kaynak görüntünün koordinatına çevirirken çıkarılır.
PAPER_PAD = 12
_PAPER_MODES = ("bright", "outline", "outline_bright")


def _ink_on_paper(mask: np.ndarray) -> np.ndarray:
    """Metin maskesini OCR'ın sevdiği biçime çevirir: beyaz zemin, siyah yazı, kenarda boşluk."""
    inv = cv2.copyMakeBorder(255 - mask, PAPER_PAD, PAPER_PAD, PAPER_PAD, PAPER_PAD, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)


def _paper_pad(mode: str) -> int:
    return PAPER_PAD if mode in _PAPER_MODES else 0


class OcrEngine:
    name = "?"

    def recognize(self, bgr: np.ndarray) -> list[OcrLine]:  # pragma: no cover - arayüz
        raise NotImplementedError

    def close(self) -> None:
        pass


class WindowsOcrEngine(OcrEngine):
    name = "Windows OCR"

    def __init__(self, upscale: str = "auto", preprocess_mode: str = "none"):
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
            from winrt.windows.media.ocr import OcrEngine as _WinOcr
        except Exception as e:  # pragma: no cover - paket eksik
            raise OcrUnavailable(f"Windows OCR bileşenleri yüklenemedi: {e}") from e

        self._SoftwareBitmap = SoftwareBitmap
        self._BGRA8 = BitmapPixelFormat.BGRA8
        engine = None
        for tag in ("en-US", "en-GB"):
            lang = Language(tag)
            if _WinOcr.is_language_supported(lang):
                engine = _WinOcr.try_create_from_language(lang)
                if engine:
                    break
        if engine is None:
            for lang in _WinOcr.available_recognizer_languages:
                if lang.language_tag.lower().startswith("en"):
                    engine = _WinOcr.try_create_from_language(lang)
                    break
        if engine is None:
            raise OcrUnavailable(
                "Windows'ta İngilizce OCR dil paketi yüklü değil.\n"
                "Ayarlar → Saat ve dil → Dil ve bölge → 'English (United States)' ekleyin\n"
                "ya da yönetici PowerShell'de çalıştırın:\n"
                "Add-WindowsCapability -Online -Name \"Language.OCR~~~en-US~0.0.1.0\"\n"
                "Alternatif: OCR motoru olarak RapidOCR'ı seçin.")
        self._engine = engine
        self._max_dim = int(getattr(engine, "max_image_dimension", 10000) or 10000)
        self._loop = asyncio.new_event_loop()
        self.upscale = upscale
        self.mode = preprocess_mode

    async def _run(self, bgra: np.ndarray):
        h, w = bgra.shape[:2]
        sb = self._SoftwareBitmap.create_copy_from_buffer(bgra, self._BGRA8, w, h)
        return await self._engine.recognize_async(sb)

    def recognize(self, bgr: np.ndarray) -> list[OcrLine]:
        if self.mode != "auto":
            return self._recognize(bgr, self.mode)
        # Otomatik: kenarlı yazı iki eşikle (her renk / yalnız çok parlak) ve "parlak metni ayır" (kenarsız
        # beyaz/sarı yazıda güçlü) birlikte okunur; daha çok geçerli İngilizce okuyan seçilir. Hepsi zayıfsa ya
        # da birbirini tutmuyorsa (biri kelime kaçırıyor) ham görüntü de denenir.
        reads: list[tuple[float, float, list[OcrLine]]] = []
        for mode in ("outline_bright", "outline", "bright", "none"):
            if mode == "none":
                best = max(reads, key=lambda r: r[:2])
                masses = [r[0] for r in reads]
                if best[1] >= 0.9 and max(masses) - min(masses) <= 0.2 * max(best[0], 1.0):
                    break
            lines = self._recognize(bgr, mode)
            text = " ".join(ln.text for ln in lines)
            reads.append((english_mass(text), english_score(text), lines))
        return max(reads, key=lambda r: r[:2])[2]

    def _recognize(self, bgr: np.ndarray, mode: str) -> list[OcrLine]:
        img, f = preprocess(bgr, self.upscale, mode)
        pad = _paper_pad(mode)
        h, w = img.shape[:2]
        if max(h, w) > self._max_dim:
            s = self._max_dim / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            f *= s
            pad *= s
        bgra = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2BGRA))
        res = self._loop.run_until_complete(self._run(bgra))
        out: list[OcrLine] = []
        for line in res.lines:
            words = []
            for word in line.words:
                r = word.bounding_rect
                words.append(OcrWord(word.text, (r.x - pad) / f, (r.y - pad) / f, r.width / f, r.height / f))
            if words:
                x, y = min(wd.x for wd in words), min(wd.y for wd in words)
                x1, y1 = max(wd.x + wd.w for wd in words), max(wd.y + wd.h for wd in words)
                out.append(OcrLine(line.text, None, x, y, x1 - x, y1 - y, tuple(words)))
            else:
                out.append(OcrLine(line.text, None, 0, 0, 0, 0))
        return out

    def close(self) -> None:
        try:
            self._loop.close()
        except Exception:
            pass


def ort_threads() -> int:
    """OCR modellerinin (onnxruntime) iş parçacığı sayısı: mantıksal işlemci sayısının dörtte biri, 1 ile 6 arası.

    Oyun ve OBS de çalışırken çekirdek kalsın diye sınırlı. Ölçüm (24 iş parçacıklı işlemci, tek satır): varsayılan
    ayarla (24 iş parçacığı, dönerek bekleme) okuma ~2 sn işlemci zamanı yakıyordu; 4 iş parçacığıyla ~48 ms /
    ~140 ms işlemci, 6 ile ~40 ms / ~150 ms işlemci, 1 ile ~75 ms / ~75 ms işlemci."""
    return max(1, min(6, (os.cpu_count() or 4) // 4))


def _ort_session(path: Path, threads: int):
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 4
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    so.intra_op_num_threads = max(1, threads)
    so.inter_op_num_threads = 1
    # İş parçacıkları çağrı bitince dönerek (spinning) beklemesin: kareler arasında boşuna çekirdek yakmasın
    so.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])


# RapidOCR'ın kullandığımız ayarları (config.yaml + RapidOcrEngine'in eski parametreleri): sonuç onunla aynı kalsın
TEXT_SCORE = 0.5  # Global.text_score: güveni bundan düşük satır atılır
REC_H, REC_W, REC_BATCH = 48, 320, 6  # Rec.rec_img_shape, Rec.rec_batch_num
PAD_RATIO, PAD_MIN_H = 8, 30  # Global.width_height_ratio, Global.min_height (yassı görüntüye dikey dolgu)
MAX_SIDE, MIN_SIDE = 2000, 30  # Global.max_side_len, Global.min_side_len
DET_LIMIT = 960  # Det.limit_side_len (limit_type: max)


class PpOcr:
    """PP-OCRv6 metin bulma (det) ve tanıma (rec) modelleri, doğrudan onnxruntime ile.

    RapidOCR'ın kendi çağrısıyla aynı sonucu verir (aynı modeller, onun ön/son işleme sınıfları; ölçümde 765 karede
    metin, kutu ve kelime kutuları birebir aynı), ama: iş parçacığı sayısı sınırlı ve boşta dönme kapalı (RapidOCR'ın
    varsayılanı her mantıksal çekirdekte iş parçacığı açar ve çağrı aralarında döndürür); bulma ve tanıma ayrı ayrı
    çağrılabilir (metin bulucu yalnız bulmayı kullanır). Tek iş parçacığından kullanılmalıdır."""

    DET_MODEL, REC_MODEL = "PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"

    def __init__(self, threads: int, rec: bool = True):
        from rapidocr.cal_rec_boxes import CalRecBoxes
        from rapidocr.ch_ppocr_det.utils import DBPostProcess
        from rapidocr.ch_ppocr_rec.typings import TextRecOutput
        from rapidocr.ch_ppocr_rec.utils import CTCLabelDecode
        from rapidocr.utils.process_img import get_rotate_crop_image, resize_image_within_bounds

        root = _rapidocr_model_dir()
        self._det = _ort_session(root / self.DET_MODEL, threads)
        self._det_in = self._det.get_inputs()[0].name
        self._post = DBPostProcess(thresh=0.3, box_thresh=0.5, max_candidates=1000, unclip_ratio=1.6,
                                   use_dilation=True, score_mode="fast")
        self._crop = get_rotate_crop_image
        self._fit = resize_image_within_bounds
        self._rec = None
        if rec:
            self._rec = _ort_session(root / self.REC_MODEL, threads)
            self._rec_in = self._rec.get_inputs()[0].name
            chars = self._rec.get_modelmeta().custom_metadata_map["character"].splitlines()
            self._decode = CTCLabelDecode(character=chars)
            self._calc_words = CalRecBoxes()
            self._rec_output = TextRecOutput
        # Kelime kutuları (vurgulu kelimelerin yeri için): okumayı değiştirmez, maliyeti ölçülemeyecek kadar az.
        # Bir hata olursa bir daha istenmez; kelimelerin yeri harf boşluklarından bulunur.
        self.word_boxes = rec

    def detect(self, img: np.ndarray, limit: int = DET_LIMIT) -> np.ndarray:
        """Yazı satırlarının kutuları (N, 4, 2), `img` koordinatında, okuma sırasıyla; yoksa (0, 4, 2)."""
        h, w = img.shape[:2]
        top = _pad_top(h, w)
        if top:  # RapidOCR gibi: çok yassı görüntüye alttan ve üstten siyah şerit
            img = cv2.copyMakeBorder(img, top, top, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        ph = h + 2 * top
        ratio = min(1.0, limit / max(ph, w))
        rh, rw = int(round(int(ph * ratio) / 32) * 32), int(round(int(w * ratio) / 32) * 32)
        if rh <= 0 or rw <= 0:
            return np.zeros((0, 4, 2), np.float32)
        # (x / 255 - 0,5) / 0,5 = (x - 127,5) / 127,5; kanal sırası BGR kalır (RapidOCR DetPreProcess ile aynı)
        blob = cv2.dnn.blobFromImage(cv2.resize(img, (rw, rh)), 1 / 127.5, mean=(127.5, 127.5, 127.5))
        boxes, _scores = self._post(self._det.run(None, {self._det_in: blob})[0], (ph, w))
        if len(boxes) < 1:
            return np.zeros((0, 4, 2), np.float32)
        boxes = _sorted_boxes(np.asarray(boxes, np.float32))
        if top:
            boxes[:, :, 1] -= top
        np.clip(boxes[:, :, 0], 0, w, out=boxes[:, :, 0])
        np.clip(boxes[:, :, 1], 0, h, out=boxes[:, :, 1])
        return boxes

    def ocr(self, img: np.ndarray) -> list[tuple[np.ndarray, str, float, tuple | None]]:
        """Bul + tanı: [(satır kutusu, metin, güven, kelimeler)]; kelimeler ((metin, güven, kutu), ...) ya da None."""
        h, w = img.shape[:2]
        ry = rx = 1.0
        if max(h, w) > MAX_SIDE or min(h, w) < MIN_SIDE:  # RapidOCR gibi: çok büyük / küçük görüntü ölçeklenir
            img, ry, rx = self._fit(img, MIN_SIDE, MAX_SIDE)
        items = self.read(img, self.detect(img))
        if (rx, ry) == (1.0, 1.0):
            return items
        scale = np.array([rx, ry], np.float32)

        def back(box):
            return (np.asarray(box, np.float32).reshape(-1, 2) * scale).tolist()
        return [(box * scale, text, conf, tuple((t, s, back(b)) for t, s, b in words) if words else None)
                for box, text, conf, words in items]

    def read(self, img: np.ndarray, boxes: np.ndarray) -> list[tuple[np.ndarray, str, float, tuple | None]]:
        """Kutulardaki satırları tanır (bkz. ocr). Boş ve güveni düşük satırlar atılır."""
        if len(boxes) == 0:
            return []
        crops = [self._crop(img, np.array(b, np.float32)) for b in boxes]
        ratios = [c.shape[1] / float(c.shape[0]) for c in crops]
        lines: list[tuple[str, float]] = [("", 0.0)] * len(crops)
        infos: list = [None] * len(crops)
        for idx in _rec_batches(ratios):
            max_ratio = max([REC_W / REC_H] + [ratios[i] for i in idx])
            batch = np.stack([_rec_input(crops[i], max_ratio) for i in idx])
            preds = self._rec.run(None, {self._rec_in: batch})[0]
            res, words = self._decode(preds, self.word_boxes, wh_ratio_list=[ratios[i] for i in idx],
                                      max_wh_ratio=max_ratio)
            for k, i in enumerate(idx):
                lines[i] = res[k]
                infos[i] = words[k] if self.word_boxes else None
        keep = [i for i in range(len(crops)) if lines[i][0].strip()]
        kept_words = self._words([crops[i] for i in keep], np.asarray([boxes[i] for i in keep], np.float32),
                                 [lines[i] for i in keep], [infos[i] for i in keep]) if keep else []
        return [(np.asarray(boxes[i], np.float32), lines[i][0], float(lines[i][1]), kept_words[j])
                for j, i in enumerate(keep) if lines[i][1] >= TEXT_SCORE]

    def _words(self, crops: list, boxes: np.ndarray, lines: list, infos: list) -> list:
        """Satırların kelime kutuları (RapidOCR CalRecBoxes); alınamazsa her satır için None."""
        none = [None] * len(crops)
        if not self.word_boxes or not all(infos):
            return none
        try:
            txts, scores = tuple(t for t, _ in lines), tuple(s for _, s in lines)
            out = self._calc_words(crops, boxes.copy(), self._rec_output(crops, txts, scores, tuple(infos)), False)
            words = [tuple((t, s, b) for t, s, b in line if b is not None) for line in out.word_results]
        except Exception:
            log.warning("RapidOCR kelime kutuları alınamadı; kelimelerin yeri harf boşluklarından bulunacak",
                        exc_info=True)
            self.word_boxes = False
            return none
        return words if len(words) == len(crops) else none


REC_SPLIT = 2.0  # bkz. _rec_batches


def _rec_batches(ratios: list[float]) -> list[list[int]]:
    """Tanıma partileri: en/boy oranına göre sıralı, en çok REC_BATCH satır (RapidOCR gibi). Partideki her satır en
    genişinin boyuna sıfırla doldurulduğu için, oranı partinin ilkinin REC_SPLIT katını geçen satır yeni partiye
    geçer: altyazı satırının yanındaki kısa tabela / HUD kırpıntıları onun genişliğine doldurulup işi katlamasın.
    Partinin en geniş satırı hiç doldurulmaz; benzer genişlikteki satırlar (iki satırlık altyazı) yine birlikte."""
    order = sorted(range(len(ratios)), key=lambda i: ratios[i])
    out: list[list[int]] = []
    for i in order:
        if out and len(out[-1]) < REC_BATCH and ratios[i] <= REC_SPLIT * max(REC_W / REC_H, ratios[out[-1][0]]):
            out[-1].append(i)
        else:
            out.append([i])
    return out


def _pad_top(h: int, w: int) -> int:
    """RapidOCR'ın dikey dolgusu (apply_vertical_padding): çok yassı ya da çok kısa görüntüde üstteki şeridin boyu."""
    if h <= PAD_MIN_H or w / h > PAD_RATIO:
        return int(abs(max(int(w / PAD_RATIO), PAD_MIN_H) * 2 - h) / 2)
    return 0


def _rec_input(crop: np.ndarray, max_ratio: float) -> np.ndarray:
    """Tanıma girdisi (RapidOCR resize_norm_img): yükseklik 48, değerler [-1, 1], sağı sıfırla doldurulmuş."""
    width = int(REC_H * max_ratio)
    h, w = crop.shape[:2]
    rw = min(width, int(math.ceil(REC_H * w / float(h))))
    img = cv2.resize(crop, (rw, REC_H)).astype(np.float32).transpose((2, 0, 1)) / 255
    img -= 0.5
    img /= 0.5
    out = np.zeros((3, REC_H, width), np.float32)
    out[:, :, :rw] = img
    return out


def _sorted_boxes(b: np.ndarray) -> np.ndarray:
    """RapidOCR TextDetector.sorted_boxes: yukarıdan aşağı satırlar (10 px eşik), satır içinde soldan sağa."""
    y = b[:, 0, 1]
    o = np.argsort(y, kind="stable")
    b, y = b[o], y[o]
    line = np.concatenate([[0], np.cumsum((np.diff(y) >= 10).astype(np.int32))])
    return b[np.lexsort((b[:, 0, 0], line))]


# Otomatik ön işlemenin ikinci geçişi: ölçümde (765 kare: sentetik sahneler + gerçek klip) tek ham okuma, her kareyi
# iki kez okuyup seçmekten daha doğruydu (tam eşleşme %85,2 / %83,0): ayıklanmış görüntü noktalamayı ve kesme
# işaretini kaybediyor. Ayıklanmış okuma yalnız ham okuma zayıfken denenir ve ancak belirgin daha çok İngilizce
# kelime çıkarırsa seçilir (%85,5; karelerin ~%4'ünde ikinci geçiş). Güç, işlem hattının kullanacağı en alttaki
# öbeğe bakar: üstte geçen tabela yazısı okumayı zayıf göstermesin.
STRONG_CONF = 0.9  # ham okumanın (en alttaki öbeğin) her satırının güveni en az bu ise...
STRONG_SCORE = 0.9  # ...ve metnin bu kadarı İngilizceyse ikinci geçiş yapılmaz
MASK_MARGIN = 1.1  # ayıklanmış okuma, bilinen kelime kütlesi ham okumanın bu katını (+1 harf) geçerse seçilir
# Ham okuma hiç satır bulamadıysa ve hızlı kenar maskesi de yazı görmüyorsa (doluluk bundan az; geçidin "yazı var"
# eşiği) ikinci geçiş yapılmaz: yazısız karede ~30 ms boşuna iş. Ölçümde ikinci geçişin boş ham okumadan kurtardığı
# karelerin doluluğu 0,0065 ve üstüydü.
EMPTY_INK = 0.003


def _joined(lines: list[OcrLine]) -> str:
    return " ".join(ln.text for ln in lines)


def _valid(lines: list[OcrLine]) -> bool:
    """Okuma, işlem hattının satırı kabul edeceği gibi geçerli mi (bkz. textproc.assemble ve is_valid)."""
    from altyazitr.textproc import assemble, is_valid  # textproc bu modülü içe aktarır: döngü olmasın

    text, _speaker, confs = assemble(lines)
    return bool(text.strip()) and is_valid(text, confs, 3, english_score(text))


def _strong(lines: list[OcrLine]) -> bool:
    from altyazitr.textproc import assemble, is_valid

    text, _speaker, confs = assemble(lines)
    if not text.strip() or not confs or min(confs) < STRONG_CONF:
        return False
    score = english_score(text)
    return score >= STRONG_SCORE and is_valid(text, confs, 3, score)


def _clearly_better(masked: list[OcrLine], raw: list[OcrLine]) -> bool:
    if not _valid(masked):
        return False
    if not _valid(raw):
        return True
    return english_mass(_joined(masked)) > english_mass(_joined(raw)) * MASK_MARGIN + 1


class RapidOcrEngine(OcrEngine):
    name = "RapidOCR (PP-OCRv6)"

    def __init__(self, upscale: str = "1", preprocess_mode: str = "none", threads: int | None = None):
        try:
            import rapidocr  # noqa: F401
        except Exception as e:  # pragma: no cover
            raise OcrUnavailable(f"RapidOCR yüklenemedi: {e}") from e
        self.threads = threads or ort_threads()
        self._ocr = None
        try:
            self._pp: PpOcr | None = PpOcr(self.threads)
        except Exception:  # paketin iç yapısı değiştiyse ya da modeller henüz inmediyse: RapidOCR'ın kendi çağrısı
            log.warning("OCR modelleri doğrudan açılamadı; RapidOCR'ın kendi çağrısı kullanılacak", exc_info=True)
            self._pp = None
            self._ocr = _rapidocr(self.threads)
            self._word_boxes = True
        log.info("RapidOCR modelleri: %s, %d iş parçacığı", _rapidocr_model_dir(), self.threads)
        self.upscale = "1" if upscale == "auto" else upscale
        self.mode = preprocess_mode

    def recognize(self, bgr: np.ndarray) -> list[OcrLine]:
        if self.mode != "auto":
            return self._recognize(bgr, self.mode)
        # Otomatik: önce ham görüntü (noktalamayı, kesme işaretini, süslü yazı tiplerini en iyi o okur). Zayıfsa
        # asfalt/kum ayıklanmış görüntü de okunur: kumlu, hareketli arka planda ve tabela/plaka yazısı karıştığında
        # öne geçer (bkz. STRONG_CONF).
        raw = self._recognize(bgr, "none")
        if _strong(raw) or (not raw and float(text_mask(bgr).mean()) < EMPTY_INK):
            return raw
        masked = self._recognize(bgr, "outline_bright")
        return masked if _clearly_better(masked, raw) else raw

    def _items(self, img: np.ndarray) -> list[tuple]:
        if self._pp is not None:
            return self._pp.ocr(img)
        r = None
        if self._word_boxes:
            try:
                r = self._ocr(img, use_cls=False, return_word_box=True)
            except Exception:
                log.warning("RapidOCR kelime kutuları alınamadı; kelimelerin yeri harf boşluklarından bulunacak",
                            exc_info=True)
                self._word_boxes = False
        if r is None:
            r = self._ocr(img, use_cls=False, return_word_box=False)
        if r is None or not getattr(r, "txts", None) or r.boxes is None:
            return []
        n = len(r.txts)
        words = getattr(r, "word_results", None) or ()
        if not self._word_boxes or len(words) != n:  # kutusu eksik satır varsa sıralar kayar: hiçbiri kullanılmaz
            words = [None] * n
        return list(zip(r.boxes, r.txts, r.scores or [None] * n, words))

    def _recognize(self, bgr: np.ndarray, mode: str) -> list[OcrLine]:
        img, f = preprocess(bgr, self.upscale, mode)
        pad = _paper_pad(mode)

        def rect(box) -> tuple[float, float, float, float]:
            b = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            x0, y0 = (b[:, 0].min() - pad) / f, (b[:, 1].min() - pad) / f
            return x0, y0, (b[:, 0].max() - pad) / f - x0, (b[:, 1].max() - pad) / f - y0

        return [OcrLine(txt, float(sc) if sc is not None else None, *rect(box), _rapid_words(wl or (), rect))
                for box, txt, sc, wl in self._items(img)]


def _rapidocr(threads: int, det_only: bool = False):
    """RapidOCR'ın kendi nesnesi (yedek yol)."""
    from rapidocr import ModelType, OCRVersion, RapidOCR

    return RapidOCR(params={
        "Global.log_level": "error",
        "Global.model_root_dir": str(_rapidocr_model_dir()),
        "Global.text_score": TEXT_SCORE,
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
        "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
        "Det.limit_type": "max", "Det.limit_side_len": 640 if det_only else DET_LIMIT,
        "EngineConfig.onnxruntime.intra_op_num_threads": threads,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })


def _rapid_words(items, rect) -> tuple[OcrWord, ...]:
    """RapidOCR'ın bir satırdaki kelimeleri [(metin, güven, kutu)]; beklenmeyen biçimde boş döner."""
    try:
        return tuple(OcrWord(str(text), *rect(box)) for text, _score, box in items if box is not None)
    except (TypeError, ValueError):
        return ()


def _rapidocr_model_dir():
    """PP-OCRv6 modelleri programla birlikte gelir (rapidocr paketi): onları kullan, ilk okumada internetten
    indirme olmasın. Paket eksikse eski yol: kullanıcının model klasörü (RapidOCR oraya indirir)."""
    import rapidocr

    bundled = Path(rapidocr.__file__).resolve().parent / "models"
    if all((bundled / n).is_file() for n in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx")):
        return bundled
    root = MODEL_DIR / "rapidocr"
    root.mkdir(parents=True, exist_ok=True)
    return root


ENGINES = {
    "rapidocr": ("RapidOCR PP-OCRv6 (önerilen)", RapidOcrEngine),
    "windows": ("Windows OCR (hızlı, İngilizce OCR dil paketi gerekir)", WindowsOcrEngine),
}


def create_engine(key: str, upscale: str = "auto", preprocess_mode: str = "none") -> OcrEngine:
    _, cls = ENGINES.get(key, ENGINES["windows"])
    return cls(upscale=upscale, preprocess_mode=preprocess_mode)
