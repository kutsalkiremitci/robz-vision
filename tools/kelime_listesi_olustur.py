"""altyazitr/data/en_words.txt dosyasını üretir (OCR kalite puanı için İngilizce kelime listesi).

Kaynak: quickmt/quickmt-en-tr modelinin kaynak sözlüğü (CC BY 4.0) + yaygın konuşma dili ekleri.
Kullanım:  python tools/kelime_listesi_olustur.py [quickmt_klasörü]
"""

import json
import os
import re
import sys
from pathlib import Path

EXTRA = """
gonna gotta wanna kinda sorta lemme gimme dunno ain't y'all ya yeah yep nope nah okay ok hey huh uh um
whoa wow damn hell crap shit fuck fucking fucked bro dude man guys cop cops homie bruh yo hmm oh ah
mom dad mama papa sis tailing tailed bail bailed busted wasted heist stash cash bucks grand dough
"""


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ["LOCALAPPDATA"]) / "RobzVision" / "modeller" / "quickmt-en-tr"
    vocab = json.loads((src / "source_vocabulary.json").read_text(encoding="utf-8"))
    words = set()
    for tok in vocab:
        if isinstance(tok, str) and tok.startswith("▁"):
            w = tok[1:]
            if re.fullmatch(r"[A-Za-z]+(?:'[a-z]+)?", w) and (len(w) > 1 or w.lower() in {"a", "i"}):
                words.add(w.lower())
    words.update(EXTRA.split())
    out = Path(__file__).resolve().parents[1] / "altyazitr" / "data" / "en_words.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    print(f"{len(words)} kelime yazıldı: {out}")


if __name__ == "__main__":
    main()
