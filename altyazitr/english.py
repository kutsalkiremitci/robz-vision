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


@lru_cache(maxsize=4096)
def english_score(text: str) -> float:
    toks = text.split()
    if not toks:
        return 0.0
    total = 0.0
    for i, raw in enumerate(toks):
        t = raw.strip(_PUNCT)
        if not t:
            total += 0.5  # yalnız noktalama ("...", "-")
            continue
        if _WORD.fullmatch(t):
            if is_known(t):
                total += 1.0
            elif t[0].isupper() and len(t) > 1 and t[1:].islower():
                total += 0.7  # büyük harfle başlayan bilinmeyen kelime: muhtemelen özel ad
            elif t.isupper() and len(t) > 1:
                total += 0.6  # kısaltma / bağıran yazı
        elif re.fullmatch(r"\d+([.,:]\d+)*%?", t):
            total += 0.6
        # harf+rakam karışımı, garip karakterler: 0 puan
    return total / len(toks)
