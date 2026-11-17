"""İki katmanlı algılama: hızlı kenar maskesi + yapay zekâ metin bulucu (açık zeminde beyaz altyazı kaçmasın)."""

import cv2
import numpy as np
import pytest

from altyazitr.detect import ChangeGate, GateParams, boxed_text_mask, shrink


def _concrete(seed: int, base: int = 210, h: int = 120, w: int = 900) -> np.ndarray:
    """Açık renkli, kumlu beton (GTA'da kaldırım)."""
    rng = np.random.default_rng(seed)
    g = cv2.GaussianBlur(rng.normal(base, 12, (h, w)).astype(np.float32), (0, 0), 1.0)
    return cv2.cvtColor(np.clip(g, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def _white_text(img: np.ndarray, text: str = "Come see me, old friend.", shadow: bool = True) -> np.ndarray:
    """GTA V gibi: beyaz yazı, dış çizgi yok; isteğe bağlı hafif, kaymış gölge."""
    org = (160, 75)
    if shadow:
        sh = img.copy()
        cv2.putText(sh, text, (org[0] + 2, org[1] + 2), cv2.FONT_HERSHEY_DUPLEX, 1.4, (120, 120, 120), 2, cv2.LINE_AA)
        img = cv2.addWeighted(cv2.GaussianBlur(sh, (0, 0), 1.2), 0.6, img, 0.4, 0)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_DUPLEX, 1.4, (246, 246, 246), 2, cv2.LINE_AA)
    return img


class _FakeDetector:
    """Gerçek modeli yüklemeden: verilen kutuları döndürür (ya da hata verir)."""

    def __init__(self, boxes=None, fail=False):
        self._boxes, self.fail, self.calls = boxes or [], fail, 0

    def boxes(self, small):
        self.calls += 1
        if self.fail:
            raise RuntimeError("model yok")
        return [np.asarray(b, np.float32) for b in self._boxes]


# yazının bulunduğu satır, 640 px'e küçültülmüş karede (900 → 640: x0.711)
_LINE = [[(105, 25), (480, 25), (480, 62), (105, 62)]]


def test_white_text_on_light_needs_detector():
    p = GateParams()
    img = _white_text(_concrete(1))
    fast_only = ChangeGate(p)
    fast_only.update(img, now=0.0)
    assert fast_only.ink < p.appear  # hızlı katman açık zeminde beyaz yazıyı göremez (eski "Metin yok")
    both = ChangeGate(p, _FakeDetector(_LINE))
    both.update(img, now=0.0)
    assert both.ink >= p.appear  # metin bulucuyla bulunur


def test_empty_concrete_no_false_text():
    p = GateParams()
    g = ChangeGate(p, _FakeDetector([]))
    for i in range(3):
        g.update(_concrete(10 + i), now=i * 0.3)
        assert g.ink < p.vanish


def test_faint_letters_fall_back_to_boxes():
    """Harfler zeminden ayrılamıyorsa (çok açık zemin, gölgesiz) bulucunun kutusu yazı sayılır."""
    small = shrink(_white_text(_concrete(3, base=240), shadow=False))
    m = boxed_text_mask(small, [np.asarray(_LINE[0], np.float32)])
    assert m.mean() > 0.05


def test_detector_runs_at_interval_and_survives_errors():
    p = GateParams(det_interval=0.2)
    det = _FakeDetector(_LINE)
    g = ChangeGate(p, det)
    img = _white_text(_concrete(4))
    for i in range(10):  # 10 kare/sn, 1 sn
        g.update(img, now=i * 0.1)
    assert det.calls == 5  # saniyede 5 kez, her karede değil
    broken = ChangeGate(p, _FakeDetector(fail=True))
    broken.update(img, now=0.0)  # hata verirse hızlı katmanla devam eder, çökmez
    assert broken.detector is None


def test_gate_reads_once_text_appears_and_clears_when_gone():
    p = GateParams()
    g = ChangeGate(p, _FakeDetector(_LINE))
    acts = []
    for i in range(30):
        has = 5 <= i < 20
        img = _white_text(_concrete(100 + i)) if has else _concrete(100 + i)
        if not has:
            g.detector._boxes = []
        else:
            g.detector._boxes = _LINE
        a = g.update(img, now=i * 0.1)
        if a:
            acts.append((i, a))
    assert acts and acts[0][1] == "ocr" and 5 <= acts[0][0] <= 10  # akan zemine rağmen kısa sürede okunur
    assert any(a == "gone" for _, a in acts)


def test_real_detector_finds_white_on_light():
    pytest.importorskip("rapidocr")
    from altyazitr.detect import TextDetector

    det = TextDetector()
    assert det.boxes(shrink(_white_text(_concrete(7)))), "beyaz yazı açık zeminde bulunamadı"
    assert not det.boxes(shrink(_concrete(8))), "boş betonda yazı bulundu"
