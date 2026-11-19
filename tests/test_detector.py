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


# --- TextGate: metin bulucuya dayalı geçit ---------------------------------------------------------------------------
_W, _H = 640, 120  # küçültülmeyen kare (geçidin çalıştığı genişlik): sahte bulucunun kutuları doğrudan kare koordinatı
_FONT, _SCALE, _THICK = cv2.FONT_HERSHEY_DUPLEX, 0.9, 2


def _dark(seed: int) -> np.ndarray:
    """Koyu, dokulu, kareden kareye değişen zemin (akan gece sahnesi)."""
    rng = np.random.default_rng(seed)
    return cv2.GaussianBlur((rng.random((_H, _W, 3)) * 90).astype(np.uint8), (0, 0), 3)


def _line_box(text: str, org: tuple[int, int]) -> list[tuple[float, float]]:
    (w, h), base = cv2.getTextSize(text, _FONT, _SCALE, _THICK)
    x0, y0, x1, y1 = org[0] - 4, org[1] - h - 4, org[0] + w + 4, org[1] + base + 4
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _subtitle(seed: int, text: str | None, alpha: float = 1.0, org=(60, 90)) -> tuple[np.ndarray, list]:
    """Kenarlı beyaz altyazı (alpha: belirme efekti) ve sahte bulucunun döndüreceği kutu."""
    img = _dark(seed)
    if not text:
        return img, []
    layer = img.copy()
    cv2.putText(layer, text, org, _FONT, _SCALE, (0, 0, 0), _THICK + 4, cv2.LINE_AA)
    cv2.putText(layer, text, org, _FONT, _SCALE, (245, 245, 245), _THICK, cv2.LINE_AA)
    return cv2.addWeighted(layer, alpha, img, 1 - alpha, 0), [_line_box(text, org)]


class _ScriptedDetector:
    """Her kare için verilen kutuları döndürür (gerçek modeli yüklemeden)."""

    def __init__(self):
        self.next: list = []
        self.calls = 0

    def boxes(self, small):
        self.calls += 1
        return [np.asarray(b, np.float32) for b in self.next]


def _run(gate, det, frames, fps: int = 20) -> list[tuple[int, object]]:
    """frames: [(kare, kutular)] → [(kare sırası, GateAction)]."""
    acts = []
    for i, (img, boxes) in enumerate(frames):
        det.next = boxes
        a = gate.update(img, now=i / fps)
        if a is not None:
            acts.append((i, a))
    return acts


def _gate(**kw):
    from altyazitr.detect import TextGate, TextGateParams

    det = _ScriptedDetector()
    return TextGate(det, TextGateParams(det_budget=1.0, **kw)), det


def test_text_gate_reads_next_frame_and_confirms_early_read():
    """Yazı belirdiği karede erken okuma ("spec"), sonraki kare aynıysa okuma o erken okumanın kimliğiyle gelir: OCR,
    yazının durulmasını beklerken çalışmış olur. Zemin akıyor (her kare farklı) ama yazı aynı."""
    gate, det = _gate()
    frames = [_subtitle(i, None) for i in range(3)] + [_subtitle(10 + i, "Get in the car, Jason!") for i in range(12)]
    acts = _run(gate, det, frames)
    (i0, spec), (i1, ocr) = acts[:2]
    assert (spec.kind, ocr.kind) == ("spec", "ocr") and (i0, i1) == (3, 4)  # belirdiği kare, bir sonraki kare
    assert ocr.spec == spec.spec  # erken okuma doğrulandı
    assert len(acts) == 2  # yazı dururken başka okuma yok (sigorta okuması 2,5 sn'de)
    assert det.calls < len(frames) - 4  # okunduktan sonra bulucu her karede çalışmaz, harf imzası yeter


def test_text_gate_new_line_same_place_and_gone():
    """Aynı yerde başka bir satır: yeniden okunur. Yazı kaybolunca gone_s sonra bir kez "gone"."""
    gate, det = _gate()
    a, b = "We need to move, now!", "Who are those guys?"
    frames = ([_subtitle(i, a) for i in range(6)] + [_subtitle(20 + i, b) for i in range(6)]
              + [_subtitle(40 + i, None) for i in range(10)])
    kinds = [(i, act.kind) for i, act in _run(gate, det, frames)]
    reads = [i for i, k in kinds if k == "ocr"]
    assert reads[0] == 1 and any(6 <= i <= 8 for i in reads[1:])  # ikinci satır en geç 2 kare sonra okunur
    gone = [i for i, k in kinds if k == "gone"]
    assert len(gone) == 1 and 12 + 4 <= gone[0] <= 12 + 7  # ~0,25 sn (5 kare) sonra


def test_text_gate_fade_in_is_not_read_half_visible():
    """Belirme efekti: yazının parlaklığı artarken erken okuma doğrulanmaz; okuma yazı tam belirdikten sonra."""
    gate, det = _gate()
    alphas = [0.2, 0.4, 0.6, 0.8, 1.0, 1.0, 1.0, 1.0]
    frames = [_subtitle(i, "I used to think this town owed me.", alpha=al) for i, al in enumerate(alphas)]
    acts = _run(gate, det, frames)
    reads = [(i, a) for i, a in acts if a.kind == "ocr"]
    assert reads and reads[0][0] >= 5  # tam görünür ilk kareden (4) sonra
    first = reads[0][1]
    spec_frames = {a.spec: i for i, a in acts if a.kind == "spec"}
    assert first.spec is None or spec_frames[first.spec] >= 4  # doğrulanan erken okuma tam görünür bir kareden


def test_text_gate_typewriter_waits_until_line_stops_growing():
    """Daktilo efekti: harfler tek tek eklenirken (kutu sağa büyüyor) okunmaz, satır durunca okunur; yarım satırın
    erken okuması doğrulanmaz."""
    gate, det = _gate()
    text = "Somebody has been lying to both of us."
    frames = [_subtitle(i, text[:n]) for i, n in enumerate(range(4, len(text) + 1, 2))]
    full_at = len(frames) - 1
    frames += [_subtitle(100 + i, text) for i in range(12)]
    acts = _run(gate, det, frames)
    stable = [(i, a) for i, a in acts if a.kind == "ocr" and a.why == "stable"]
    assert stable and stable[0][0] > full_at
    spec_frames = {a.spec: i for i, a in acts if a.kind == "spec"}
    for _i, a in stable:
        assert a.spec is None or spec_frames[a.spec] >= full_at


def test_text_gate_slow_typewriter_line_is_not_read_half_way():
    """Yazılması 1,5 sn'den uzun süren daktilo satırı da (eski geçidin "en geç" ara okuması) yarım okunmaz. Bilinen
    sınır: daktilo olduğu ilk harflerde anlaşılmaz; ilk kelime bir kez okunabilir (satır uzayınca güncellenir)."""
    gate, det = _gate()
    text = "Wait... Is that him? No way. He said he left town!"
    frames = [_subtitle(i, text[:n]) for i, n in enumerate(range(3, len(text) + 1))]  # 20 harf/sn, ~2,4 sn
    full_at = len(frames) - 1
    frames += [_subtitle(200 + i, text) for i in range(12)]
    reads = [i for i, a in _run(gate, det, frames) if a.kind == "ocr"]
    assert [i for i in reads if 3 <= i <= full_at] == []  # daktilo anlaşıldıktan sonra satır bitene kadar okuma yok
    assert reads[-1] > full_at


def test_text_gate_ignores_sign_above_subtitle():
    """Altyazının üstünde beliren ayrı yazı (tabela, HUD) yalnız en alttaki öbek okunurken değişiklik sayılmaz."""
    gate, det = _gate()
    sub = "Then stop complaining and grab the bag."
    frames = [_subtitle(i, sub) for i in range(6)]
    for i in range(6):
        img, boxes = _subtitle(30 + i, sub)
        cv2.putText(img, "MOTEL", (250, 25), _FONT, 0.7, (200, 230, 250), 2, cv2.LINE_AA)
        frames.append((img, [_line_box("MOTEL", (250, 25)), *boxes]))
    acts = _run(gate, det, frames)
    assert [a.kind for _i, a in acts] == ["spec", "ocr"]


def test_text_gate_detector_budget_skips_frames_on_slow_cpu():
    """Bulucu yavaşsa (zayıf işlemci) payını aşmaz: çağrıları arasında kendi süresinin katı kadar bekler."""
    import time as _time

    from altyazitr.detect import TextGate, TextGateParams

    class _Slow(_ScriptedDetector):
        def boxes(self, small):
            _time.sleep(0.02)
            return super().boxes(small)

    det = _Slow()
    gate = TextGate(det, TextGateParams(det_budget=0.25))
    img, _ = _subtitle(1, None)
    t0 = _time.perf_counter()
    while _time.perf_counter() - t0 < 0.5:
        gate.update(img)
    assert det.calls <= 0.5 / 0.02 * 0.25 + 2  # en çok zamanın ~%25'i bulucuda
