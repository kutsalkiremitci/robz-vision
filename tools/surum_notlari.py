"""GitHub sürüm sayfasının metnini oluşturur: indirme rehberi + CHANGELOG.md'deki ilgili bölüm.

Kullanım:  python tools/surum_notlari.py 1.0.0 surum_notlari.md
(.github/workflows/release.yml etiketli sürümlerde çalıştırır.)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REHBER = """\
## Hangi dosyayı indirmeliyim?

| Dosya | Ne için |
|---|---|
| **`RobzVision-Setup-{v}-x64.exe`** | Çoğu kullanıcı için bu. Çalıştırıp sihirbazda **İleri**'ye basın. Yönetici izni istemez, Başlat menüsüne eklenir, *Ayarlar → Uygulamalar*'dan kaldırılır. |
| `RobzVision-Portable-{v}-x64.zip` | Kurulum yapmadan kullanmak için. Kısa bir klasöre (ör. `C:\\RobzVision`) çıkarıp `RobzVision.exe`'yi açın. |
| `RobzVision-Portable-{v}-x64.7z` | Taşınabilir sürümün daha küçük arşivi. Windows 11 ya da 7-Zip ile açılır. |
| `SHA256SUMS.txt` | İndirdiğiniz dosyanın bozulmadığını doğrulamak için özetler. |

**Sistem.** Windows 10 (2004 ve sonrası) ya da Windows 11, 64 bit. Windows'un İngilizce OCR dil paketi gerekir
(ayrıntılar README'de). İlk açılışta ücretsiz çeviri modeli (yaklaşık 400 MB) bir kez indirilir.

**ARM işlemcili bilgisayarlar** (ör. Snapdragon). Ayrı bir ARM64 sürümü yok: çeviri motorunun (CTranslate2)
ve birkaç kütüphanenin ARM64 Windows paketi bulunmuyor. Windows 11 x64 programları öykünmeyle çalıştırabildiği
için kurulum dosyası ARM'li Windows 11'e de kurulur. ARM cihazlarda henüz denenmedi; sorun yaşarsanız bildirin.

**Güvenlik uyarısı.** Dosyalar dijital olarak imzalı değil. SmartScreen uyarısında *Ek bilgi → Yine de çalıştır*'ı
seçebilirsiniz. Doğrulamak için PowerShell'de `Get-FileHash .\\RobzVision-Setup-{v}-x64.exe` çıktısını
`SHA256SUMS.txt` ile karşılaştırın.
"""


def changelog_section(version: str, text: str) -> str:
    """CHANGELOG.md'den '## [sürüm]' bölümünün gövdesini döndürür (başlık ve bağlantı tanımları hariç)."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(f"## [{version}]")), None)
    if start is None:
        raise SystemExit(f"CHANGELOG.md içinde '## [{version}]' bölümü yok.")
    body: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## [") or re.match(r"^\[[^\]]+\]:\s", line):
            break
        body.append(line)
    return "\n".join(body).strip()


def main() -> None:
    # GitHub'ın Windows makinesinde konsol cp1252: Türkçe harfler print'te hata vermesin
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    version, out = sys.argv[1], Path(sys.argv[2])
    notes = changelog_section(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    out.write_text(REHBER.format(v=version) + "\n## Bu sürümde\n\n" + notes + "\n", encoding="utf-8")
    print(f"Sürüm notları yazıldı: {out}")


if __name__ == "__main__":
    main()
