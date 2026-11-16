"""OCR çıktısını temizleme: satır birleştirme, tipik OCR hatalarını düzeltme,
konuşmacı adını ayırma, çöp metni eleme, karşılaştırma anahtarı, cümle bölme."""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

from altyazitr.ocr import OcrLine

# --- OCR düzeltmeleri ------------------------------------------------------
_FIXES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<=\w)[_•·»«](?=\w)"), " "),  # kelimeler arasına karışan gürültü karakterleri
    (re.compile(r"[•·»«]"), ""),
    (re.compile(r"\|"), "I"),
    (re.compile(r"(?<![\w'])l(?=['’](m|ll|d|ve)\b)"), "I"),  # l'm -> I'm
    (re.compile(r"(?<![\w'’])l(?![\w'’])"), "I"),  # tek başına l -> I
    (re.compile(r"(?<=[A-Za-z])0(?=[A-Za-z])"), "O"),
    (re.compile(r"[‘’`´]"), "'"),
    (re.compile(r"[“”„]"), '"'),
    (re.compile(r"…"), "..."),
    (re.compile(r"\s+([,.!?;:])"), r"\1"),
    (re.compile(r"([!?])\1{2,}"), r"\1\1"),
    (re.compile(r"\s{2,}"), " "),
]


def fix_ocr(s: str) -> str:
    for pat, rep in _FIXES:
        s = pat.sub(rep, s)
    s = s.strip(" _~=*•·")
    s = re.sub(r"^[-—–]+\s*", "", s)  # diyalog tiresi; sondaki tire satır birleştirme için korunur
    return s.strip()


def norm_key(s: str) -> str:
    """Karşılaştırma/önbellek anahtarı: küçük harf, yalnızca harf/rakam/kesme işareti."""
    s = s.casefold()
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# --- satırları birleştirme ------------------------------------------------
def _rows(lines: Sequence[OcrLine]) -> list[list[OcrLine]]:
    """Aynı yatay hizadaki parçaları (RapidOCR bir satırı bölebilir) tek satırda toplar."""
    items = sorted([ln for ln in lines if ln.text.strip()], key=lambda ln: ln.y + ln.h / 2)
    rows: list[list[OcrLine]] = []
    for ln in items:
        cy = ln.y + ln.h / 2
        if rows:
            last = rows[-1]
            lcy = statistics.fmean(x.y + x.h / 2 for x in last)
            lh = max(1.0, statistics.fmean(max(1.0, x.h) for x in last))
            if abs(cy - lcy) < 0.5 * lh:
                last.append(ln)
                continue
        rows.append([ln])
    return [sorted(r, key=lambda ln: ln.x) for r in rows]


SPEAKER_RE = re.compile(r"^\s*([A-Z][A-Za-z0-9.'\-]*(?:\s+[A-Z][A-Za-z0-9.'\-]*){0,2})\s*:(?:\s+|(?=[A-Z\"']))(.+)$",
                        re.S)
_NOT_SPEAKERS = {
    "objective", "new objective", "hint", "tip", "note", "warning", "mission", "task", "goal",
    "reward", "location", "press", "hold", "tap", "chapter", "quest", "new quest", "info", "tutorial",
    "mission failed", "mission passed", "wasted", "busted",
}


def split_speaker(text: str) -> tuple[str | None, str]:
    m = SPEAKER_RE.match(text)
    if not m:
        return None, text
    name = m.group(1).strip()
    if name.casefold() in _NOT_SPEAKERS or len(name) > 26:
        return None, text
    return name, m.group(2).strip()


def assemble(lines: Sequence[OcrLine], keep_lowest_block: bool = True,
             detect_speaker: bool = True) -> tuple[str, str | None, list[float]]:
    """OCR satırlarını tek altyazı metnine çevirir: (metin, konuşmacı, güven değerleri)."""
    rows = _rows(lines)
    if not rows:
        return "", None, []
    blocks: list[list[list[OcrLine]]] = [[rows[0]]]
    heights = [max(1.0, max(x.h for x in r)) for r in rows]
    hm = statistics.median(heights) if heights else 1.0
    for prev, row in zip(rows, rows[1:]):
        prev_bottom = max(x.y + x.h for x in prev)
        top = min(x.y for x in row)
        if top - prev_bottom > 1.2 * hm:
            blocks.append([row])
        else:
            blocks[-1].append(row)
    chosen = blocks[-1] if keep_lowest_block else [r for b in blocks for r in b]
    row_texts = [" ".join(x.text.strip() for x in r) for r in chosen]
    confs = [x.conf for r in chosen for x in r if x.conf is not None]

    speaker = None
    if detect_speaker and row_texts:
        speaker, first = split_speaker(fix_ocr(row_texts[0]))
        if speaker:
            row_texts[0] = first
    text = fix_ocr(" ".join(row_texts))
    text = re.sub(r"(?<=[a-z])- (?=[a-z])", "", text)  # satır sonu tirelerini birleştir
    return text, speaker, confs


def is_valid(text: str, confs: Sequence[float], min_letters: int = 3, score: float | None = None) -> bool:
    letters = sum(c.isalpha() for c in text)
    nonspace = sum(not c.isspace() for c in text)
    if letters < min_letters or nonspace == 0:
        return False
    if letters / nonspace < 0.6:
        return False
    if confs and sum(confs) / len(confs) < 0.55:
        return False
    # 3+ kelimelik metinde gerçek İngilizce kelime oranı çok düşükse çöp OCR'dır (HUD, doku, gürültü)
    if score is not None and len(text.split()) >= 3 and score < 0.35:
        return False
    return True


# --- cümle bölme (NMT modelleri çok cümleli girdide cümle düşürebiliyor) --
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "prof", "lt", "sgt", "capt", "gen", "col",
           "mt", "no", "etc", "e.g", "i.e", "u.s", "a.m", "p.m"}
_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9¿¡])")


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for m in _BOUNDARY.finditer(text):
        before = text[start:m.start()].rstrip("\"')]")
        last_word = before.rsplit(" ", 1)[-1].rstrip(".!?").casefold()
        if before.endswith(".") and (last_word in _ABBREV or (len(last_word) == 1 and last_word.isalpha())):
            continue
        parts.append(text[start:m.end()].strip())
        start = m.end()
    parts.append(text[start:].strip())
    return [p for p in parts if p]
