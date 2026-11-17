"""OCR motorları.

* WindowsOcrEngine — Windows'un yerleşik OCR'ı (Windows.Media.Ocr). İndirme gerektirmez,
  ~10 ms. Varsayılan ve önerilen motor.
* RapidOcrEngine — RapidOCR + PP-OCRv6 (ONNX, CPU). Süslü/zor fontlarda alternatif, ~50 ms.

Her motor yalnızca *tek bir* iş parçacığında (OCR iş parçacığı) oluşturulup kullanılmalıdır.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import cv2
import numpy as np

from altyazitr.detect import outlined_text_mask
from altyazitr.english import english_mass, english_score
from altyazitr.paths import MODEL_DIR

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    conf: float | None
    x: float
    y: float
    w: float
    h: float


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


def _ink_on_paper(mask: np.ndarray) -> np.ndarray:
    """Metin maskesini OCR'ın sevdiği biçime çevirir: beyaz zemin, siyah yazı, kenarda boşluk."""
    inv = cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)


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
        h, w = img.shape[:2]
        if max(h, w) > self._max_dim:
            s = self._max_dim / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            f *= s
        bgra = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2BGRA))
        res = self._loop.run_until_complete(self._run(bgra))
        out: list[OcrLine] = []
        for line in res.lines:
            xs, ys, xe, ye = [], [], [], []
            for word in line.words:
                r = word.bounding_rect
                xs.append(r.x)
                ys.append(r.y)
                xe.append(r.x + r.width)
                ye.append(r.y + r.height)
            if xs:
                x, y = min(xs) / f, min(ys) / f
                out.append(OcrLine(line.text, None, x, y, max(xe) / f - x, max(ye) / f - y))
            else:
                out.append(OcrLine(line.text, None, 0, 0, 0, 0))
        return out

    def close(self) -> None:
        try:
            self._loop.close()
        except Exception:
            pass


class RapidOcrEngine(OcrEngine):
    name = "RapidOCR (PP-OCRv6)"

    def __init__(self, upscale: str = "1", preprocess_mode: str = "none"):
        try:
            from rapidocr import ModelType, OCRVersion, RapidOCR
        except Exception as e:  # pragma: no cover
            raise OcrUnavailable(f"RapidOCR yüklenemedi: {e}") from e
        root = MODEL_DIR / "rapidocr"
        root.mkdir(parents=True, exist_ok=True)
        self._ocr = RapidOCR(params={
            "Global.log_level": "error",
            "Global.model_root_dir": str(root),
            "Global.text_score": 0.5,
            "Det.ocr_version": OCRVersion.PPOCRV6, "Det.model_type": ModelType.SMALL,
            "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.model_type": ModelType.SMALL,
            "Det.limit_type": "max", "Det.limit_side_len": 960,
        })
        self.upscale = "1" if upscale == "auto" else upscale
        self.mode = preprocess_mode

    def recognize(self, bgr: np.ndarray) -> list[OcrLine]:
        # RapidOCR doğal görüntülerle eğitildiği için "otomatik" modda ham görüntü kullanılır
        img, f = preprocess(bgr, self.upscale, "none" if self.mode == "auto" else self.mode)
        r = self._ocr(img, use_cls=False)
        if r is None or not getattr(r, "txts", None):
            return []
        out = []
        boxes = r.boxes if r.boxes is not None else [None] * len(r.txts)
        for box, txt, sc in zip(boxes, r.txts, r.scores or [None] * len(r.txts)):
            if box is None:
                out.append(OcrLine(txt, sc, 0, 0, 0, 0))
                continue
            b = np.asarray(box, dtype=np.float32)
            x0, y0 = b[:, 0].min() / f, b[:, 1].min() / f
            x1, y1 = b[:, 0].max() / f, b[:, 1].max() / f
            out.append(OcrLine(txt, float(sc) if sc is not None else None, x0, y0, x1 - x0, y1 - y0))
        return out


ENGINES = {
    "windows": ("Windows OCR (hızlı, önerilen)", WindowsOcrEngine),
    "rapidocr": ("RapidOCR PP-OCRv6 (zor fontlar için)", RapidOcrEngine),
}


def create_engine(key: str, upscale: str = "auto", preprocess_mode: str = "none") -> OcrEngine:
    _, cls = ENGINES.get(key, ENGINES["windows"])
    return cls(upscale=upscale, preprocess_mode=preprocess_mode)
