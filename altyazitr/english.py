"""OCR çıktısının ne kadar "gerçek İngilizce" olduğunu puanlar (0..1).

Kullanım: aynı görüntünün farklı ön işlemelerle okunmuş sürümlerinden en iyisini seçmek,
çöp OCR'ı elemek ve aynı altyazının daha iyi bir okuması gelince çeviriyi düzeltmek.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from altyazitr.paths import resource_dir

log = logging.getLogger(__name__)

_SUFFIXES = ("'s", "n't", "'ll", "'re", "'ve", "'d", "'m", "s'", "ing", "ed", "es", "s", "ly", "er", "est",
             "ness", "ment", "ful", "less", "ers", "ings")
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_PUNCT = ".,!?\"()[]:;-…'*"


@lru_cache(maxsize=1)
def _words() -> frozenset[str]:
    p = resource_dir() / "data" / "en_words.txt"
    try:
        return frozenset(w.strip() for w in p.read_text(encoding="utf-8").splitlines() if w.strip())
    except OSError:
        log.warning("Kelime listesi bulunamadı: %s", p)
        return frozenset()


def is_known(word: str) -> bool:
    w = word.lower()
    words = _words()
    if not words or w in words:
        return True
    for s in _SUFFIXES:
        if w.endswith(s) and len(w) - len(s) >= 2:
            stem = w[: -len(s)]
            if stem in words or stem + "e" in words or (len(stem) > 2 and stem[-1] == stem[-2] and stem[:-1] in words):
                return True
    return False


def _weight(raw: str) -> tuple[float, int]:
    """Bir parçanın İngilizce ağırlığı (0..1) ve noktalamasız uzunluğu."""
    t = raw.strip(_PUNCT)
    if not t:
        return 0.5, 0  # yalnız noktalama ("...", "-")
    if _WORD.fullmatch(t):
        if is_known(t):
            return 1.0, len(t)
        if t[0].isupper() and len(t) > 1 and t[1:].islower():
            return 0.7, len(t)  # büyük harfle başlayan bilinmeyen kelime: muhtemelen özel ad
        if t.isupper() and len(t) > 1:
            return 0.6, len(t)  # kısaltma / bağıran yazı
    elif re.fullmatch(r"\d+([.,:]\d+)*%?", t):
        return 0.6, len(t)
    return 0.0, len(t)  # harf+rakam karışımı, garip karakterler


@lru_cache(maxsize=4096)
def english_score(text: str) -> float:
    """Okumanın ne kadarı İngilizce (0..1): temizlik ölçüsü."""
    toks = text.split()
    if not toks:
        return 0.0
    return sum(_weight(t)[0] for t in toks) / len(toks)


@lru_cache(maxsize=4096)
def english_mass(text: str) -> float:
    """Sözlükte bilinen İngilizce kelimelerin harf toplamı (+ sayılar): kapsam ölçüsü.

    Aynı karenin farklı okumalarını karşılaştırırken kelime düşüren okuma english_score'u düşürmez
    ("They're need to move" hâlâ tamamen İngilizce), ama bunu düşürür. Bilinmeyen kelimeler (büyük harfli
    olsalar bile) sayılmaz: arka plandaki plaka ya da tabela yazısı ("LAMARG") ham okumayı öne geçirmesin."""
    total = 0.0
    for raw in text.split():
        t = raw.strip(_PUNCT)
        if _WORD.fullmatch(t) and is_known(t):
            total += len(t)
        elif t and re.fullmatch(r"\d+([.,:]\d+)*%?", t):
            total += 0.6 * len(t)
    return total
