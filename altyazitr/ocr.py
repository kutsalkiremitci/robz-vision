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
        img = _ink_on_paper(outlined_text_mask(img))
    return img, f


def _ink_on_paper(mask: np.ndarray) -> np.ndarray:
    """Metin maskesini OCR'ın sevdiği biçime çevirir: beyaz zemin, siyah yazı, kenarda boşluk."""
    inv = cv2.copyMakeBorder(255 - mask, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    return cv2.cvtColor(inv, cv2.COLOR_GRAY2BGR)


_RING = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def outlined_text_mask(bgr: np.ndarray, contrast: int = 80, ratio: float = 0.4, min_area: int = 3) -> np.ndarray:
    """Koyu kenarlı/gölgeli parlak yazıyı (oyun altyazılarının çoğu) kumlu, hareketli arka plandan ayırır.

    Beyaz/sarı her bağlı parçanın çevresindeki 2 px'lik halkaya bakılır: halkanın en az `ratio` kadarı
    parçadan `contrast` birim daha koyuysa (yazının kenarı/gölgesi) parça yazıdır. Asfalt kumlanması, yol
    çizgisi, far gibi parlak şeylerin çevresi orta tonlu olduğu için elenir. Binlerce parçada da hızlı
    olsun diye döngüsüz (bincount) hesaplanır. 255 = yazı."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]
    cand = cv2.inRange(hsv, (0, 0, 200), (180, 45, 255)) | cv2.inRange(hsv, (15, 80, 170), (40, 255, 255))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    if n <= 1:
        return np.zeros_like(cand)
    area = stats[:, cv2.CC_STAT_AREA]
    vmean = np.bincount(lab.ravel(), weights=v.ravel().astype(np.float64), minlength=n) / np.maximum(area, 1)
    # halka pikseli: parçaya ait değil ama 2 px içinde bir parça var (komşu parçanın etiketi alınır)
    near = cv2.dilate(lab.astype(np.float32), _RING).astype(np.int32)
    ring = (cand == 0) & (near > 0)
    rl = near[ring]
    dark = v[ring].astype(np.float64) < vmean[rl] - contrast
    frac = np.bincount(rl, weights=dark, minlength=n) / np.maximum(np.bincount(rl, minlength=n), 1)
    height = stats[:, cv2.CC_STAT_HEIGHT]
    keep = (frac >= ratio) & (area >= min_area) & (height <= bgr.shape[0] * 0.6)
    keep[0] = False
    if keep.any():
        # Harf boyunu alana göre ağırlıklı medyanla bul (tek tek taneler sayıca çok, alanca az). Küçük parçalar
        # (i noktası, virgül, nokta) yalnızca harflere yakınsa kalır; yazıdan uzak tek tük taneler gider.
        hs, ws = height[keep], area[keep]
        order = np.argsort(hs)
        cum = np.cumsum(ws[order])
        h_text = float(hs[order][np.searchsorted(cum, cum[-1] / 2)])
        big = keep & (height >= 0.4 * h_text)
        r = int(max(2, 0.8 * h_text))
        zone = cv2.dilate(big[lab].astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8))  # dikdörtgen: ayrışabilir, hızlı
        near = np.bincount(lab.ravel(), weights=zone.ravel(), minlength=n) > 0
        keep &= big | near
    return (keep[lab] * 255).astype(np.uint8)


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
        # Otomatik: "kenarlı yazıyı ayır" (kumlu/hareketli arka planda en temizi) ve "parlak metni ayır"
        # (kenarsız beyaz/sarı yazıda güçlü) birlikte okunur; daha çok geçerli İngilizce okuyan seçilir.
        # İkisi de zayıfsa ham görüntü de denenir.
        best: tuple[float, float, list[OcrLine]] | None = None
        for mode in ("outline", "bright", "none"):
            lines = self._recognize(bgr, mode)
            text = " ".join(ln.text for ln in lines)
            cand = (english_mass(text), english_score(text), lines)
            if best is None or cand[:2] > best[:2]:
                best = cand
            if mode == "bright" and best[1] >= 0.9:
                break
        return best[2]

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
