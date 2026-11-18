"""OCR çıktısını temizleme: satır birleştirme, tipik OCR hatalarını düzeltme,
konuşmacı adını ayırma, çöp metni eleme, karşılaştırma anahtarı, cümle bölme; vurgulu (renkli) kelimelerin
metindeki yeri ve çevirmene giden [[...]] işaretleri."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence
from difflib import SequenceMatcher

import numpy as np

from altyazitr.detect import word_colors
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
    hm = statistics.median(heights)
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


# --- vurgulu kelimeler ---------------------------------------------------------------------------------------------
# Oyunun renkli yazdığı kelimeler (görev yazısındaki yer, kişi, eşya adları). Metinle birlikte karakter aralıkları
# [(başlangıç, bitiş)] olarak taşınır. Çevirmene (yapay zekâ) giden metinde ve çeviri önbelleğinde [[...]]
# işaretleriyle yazılır: "Drive to the [[radar station]]" → "[[Radar istasyonuna]] sür". İşaretli metin ekrana hiç
# gitmez: arayüze düz metin ve aralıklar gider.
HL_OPEN, HL_CLOSE = "[[", "]]"
Spans = list[tuple[int, int]]

_MARK_RE = re.compile(r"\[\[(.*?)\]\]", re.S)
_LEAD_PUNCT = "\"'“‘«([{¿¡-—–*"
_TRAIL_PUNCT = ".,!?;:\"'”’»)]}…-—–*"


def can_mark(text: str) -> bool:
    """Metin işaretlenebilir mi: köşeli ayraç içeren metinde işaretler ayırt edilemez (vurgu yok sayılır)."""
    return "[" not in text and "]" not in text


def mark(text: str, spans: Sequence[Sequence[int]]) -> str:
    """Düz metin + aralıklar → işaretli metin."""
    out, at = [], 0
    for s, e in spans:
        out += [text[at:s], HL_OPEN, text[s:e], HL_CLOSE]
        at = e
    out.append(text[at:])
    return "".join(out)


def parse_marks(s: str) -> tuple[str, Spans] | None:
    """İşaretli metin → (düz metin, aralıklar). İşaretler iyi biçimli değilse (iç içe, kapanmamış, tek ayraç) None."""
    plain: list[str] = []
    spans: Spans = []
    n = pos = 0
    for m in _MARK_RE.finditer(s):
        before, inner = s[pos:m.start()], m.group(1)
        if not can_mark(before) or not can_mark(inner):
            return None
        plain += [before, inner]
        n += len(before)
        spans.append((n, n + len(inner)))
        n += len(inner)
        pos = m.end()
    rest = s[pos:]
    if not can_mark(rest):
        return None
    plain.append(rest)
    return "".join(plain), spans


def strip_marks(s: str) -> str:
    """İşaretleri (ve bozuk işaretten kalan tek ayraçları) siler. Yalnız köşeli ayraçsız kaynaktan gelen metinde
    kullanılır: kaynak işaretlenebildiyse (bkz. can_mark) çeviride görünen her ayraç işarettir."""
    s = re.sub(r"\s{2,}", " ", s.replace("[", "").replace("]", ""))
    return re.sub(r"\s+([,.!?;:])", r"\1", s).strip()


def unmark(s: str) -> tuple[str, Spans]:
    """İşaretli metin → (düz metin, aralıklar); işaretler bozuksa vurgusuz düz metin."""
    parsed = parse_marks(s)
    return parsed if parsed is not None else (strip_marks(s), [])


def without_marks(s: str) -> str:
    """İşaretli olabilen metnin düz hali; işaretsiz metin (köşeli ayraçlı olsa da) olduğu gibi kalır."""
    if HL_OPEN not in s:
        return s
    parsed = parse_marks(s)
    return parsed[0] if parsed is not None else s


def snap_spans(text: str, spans: Sequence[Sequence[int]], whole_words: bool = False) -> Spans:
    """Aralıkların kenarındaki boşluk ve noktalamayı dışarıda bırakır ("cops." → "cops"); whole_words ise yarım
    kalan kelimeyi tamamlar ("[[Lucia]]'yı" → "Lucia'yı"). Boşalan aralık atılır, çakışanlar birleşir."""
    out: Spans = []
    for s, e in sorted((int(a), int(b)) for a, b in spans):
        s, e = max(0, s), min(len(text), e)
        while s < e and (text[s].isspace() or text[s] in _LEAD_PUNCT):
            s += 1
        while e > s and (text[e - 1].isspace() or text[e - 1] in _TRAIL_PUNCT):
            e -= 1
        if s >= e:
            continue
        if whole_words:
            while s > 0 and text[s - 1].isalnum():
                s -= 1
            while e < len(text) and (text[e].isalnum() or (text[e] in "'’" and text[e + 1:e + 2].isalnum())):
                e += 1
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(e, out[-1][1]))
        else:
            out.append((s, e))
    return out


def clean_marks(out: str, expected: int, exact: bool = True) -> str:
    """Çevirmenin işaretli çıktısını denetler. İşaretler iyi biçimliyse, sayıları kaynaktakiyle aynıysa (exact
    False ise en fazla o kadar) ve her biri bir kelime içeriyorsa vurgular kelimelere oturtulur; değilse vurgusuz
    düz metin döner. Böylece işaret karakterleri asla ekrana çıkmaz."""
    parsed = parse_marks(out)
    if parsed is not None:
        plain, spans = parsed
        n = len(spans)
        if 0 < expected and (n == expected if exact else n <= expected) and all(
                any(c.isalnum() for c in plain[s:e]) for s, e in spans):
            lead = len(plain) - len(plain.lstrip())  # "[[ Radar…" gibi işaretin içindeki baş boşluğu da gider
            plain = plain.strip()
            spans = [(s - lead, e - lead) for s, e in spans]
            return mark(plain, snap_spans(plain, spans, whole_words=True))
    return strip_marks(out)


def match_initial_case(src: str, out: str) -> str:
    """Kaynak büyük harfle başlıyorsa çevirinin ilk harfini de büyütür (Türkçe: i → İ). Küçük modeller cümle başı
    işaretliyken ilk harfi küçük yazabiliyor: "[[radar istasyonuna]] sür" → "[[Radar istasyonuna]] sür"."""
    first = next((c for c in src if c.isalpha()), "")
    if not first.isupper():
        return out
    for i, c in enumerate(out):
        if c.isalpha():
            up = {"i": "İ", "ı": "I"}.get(c, c.upper())
            return out[:i] + up + out[i + 1:] if c.islower() and len(up) == 1 else out
        if not (c.isspace() or c in "[\"'“‘«(¿¡-—–"):
            return out
    return out


def _tok_key(tok: str) -> str:
    """Kelime eşleştirme anahtarı: OCR düzeltmesinden sonra küçük harf, yalnız harf ve rakam."""
    return re.sub(r"[\W_]+", "", fix_ocr(tok).casefold())


def _token_ranges(line: OcrLine, tokens: Sequence[str]) -> list[tuple[float, float]] | None:
    """Motorun kelime kutularından her kelimenin yatay aralığı. RapidOCR bir kelimeyi parçalara bölebilir; parçalar
    birleştirilir. Kutular satırın metniyle tutmazsa None (aralıklar harf boşluklarından bulunur)."""
    if not line.words:
        return None
    words = iter(line.words)
    out = []
    for tok in tokens:
        acc, x0, x1 = "", math.inf, -math.inf
        while acc != tok:
            wd = next(words, None)
            if wd is None:
                return None
            acc += "".join(wd.text.split())
            if not tok.startswith(acc):
                return None
            x0, x1 = min(x0, wd.x), max(x1, wd.x + wd.w)
        out.append((x0, x1))
    return out if next(words, None) is None else None


def line_colors(bgr: np.ndarray, line: OcrLine) -> list[tuple[str, bool | None]]:
    """OCR satırının kelimeleri ve renkleri: True renkli, False beyaz/gri, None belirsiz (bkz. word_colors)."""
    tokens = line.text.split()
    if not tokens:
        return []
    flags = word_colors(bgr, (line.x, line.y, line.w, line.h), _token_ranges(line, tokens), [len(t) for t in tokens])
    return list(zip(tokens, flags))


def align_spans(tokens: Sequence[tuple[str, bool | None]], text: str) -> Spans:
    """OCR kelimelerinin renklerini (okuma sırasıyla) birleştirilmiş metne taşır ve renkli kelime dizilerini vurgu
    aralığı yapar. Metin assemble'dan geçtiği için kelimeler bire bir aynı olmayabilir: konuşmacı adı ve başka
    bloklar düşer, OCR düzeltmeleri harf değiştirir, satır sonu tiresi iki kelimeyi birleştirir; kelimeler sırayla
    hizalanır. Metinde beyaz/gri (gövde) kelime yoksa (altyazının tamamı renkli, ör. sarı) vurgu yoktur."""
    dst = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not dst or not tokens:
        return []
    src_keys = [_tok_key(t) for t, _ in tokens]
    dst_keys = [_tok_key(text[s:e]) for s, e in dst]
    flags: list[bool | None] = [None] * len(dst)
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, src_keys, dst_keys, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            flags[j1:j2] = [f for _, f in tokens[i1:i2]]
        elif tag == "replace":  # birleşen ya da bölünen kelimeler: hepsi aynı renkteyse o renk
            colors = {f for _, f in tokens[i1:i2]}
            if len(colors) == 1:
                flags[j1:j2] = [colors.pop()] * (j2 - j1)
    if False not in flags:
        return []
    spans: Spans = []
    run: list[int] = []  # süren vurgunun kelimeleri
    unsure: list[int] = []  # vurgudan sonra gelen belirsiz kelimeler: arkalarından vurgu gelirse köprülenir
    for j, f in enumerate([*flags, False]):
        if f is True:
            run += [*unsure, j]
            unsure = []
        elif f is None and run:
            unsure.append(j)
        else:
            if run:
                spans.append((dst[run[0]][0], dst[run[-1]][1]))
            run, unsure = [], []
    return snap_spans(text, spans)


def highlight_spans(bgr: np.ndarray, lines: Sequence[OcrLine], text: str) -> Spans:
    """OCR satırlarındaki renkli kelimeler → birleştirilmiş metindeki (bkz. assemble) vurgu aralıkları. Köşeli
    ayraçlı metinde vurgu aranmaz (işaretlerle karışır)."""
    if not can_mark(text):
        return []
    tokens = [tc for row in _rows(lines) for ln in row for tc in line_colors(bgr, ln)]
    return align_spans(tokens, text)
