# Katkı rehberi

Robz Vision küçük, gönüllü bir proje. Hata bildirimi, oyuna özel ayar önerisi ve kod katkısı memnuniyetle
karşılanır. Bu sayfa kısa tutuldu; aklınıza takılan bir şey olursa bir öneri kaydı açın.

## Geliştirme ortamı

Windows 10 (2004 ve sonrası) ya da Windows 11 ve Python 3.12 gerekir. Uygulama Windows'a özgü API'ler
(Windows OCR, Windows Graphics Capture, `SetWindowDisplayAffinity`) kullandığı için başka işletim
sistemlerinde çalışmaz.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m altyazitr --debug
```

Oyun olmadan denemek için `.venv\Scripts\python tools\test_penceresi.py` İngilizce altyazılı sahte bir oyun
penceresi açar. Robz Vision'da bu pencerenin altyazı alanını seçin.

## Testler

```powershell
.venv\Scripts\python -m pytest -q tests
```

Testler her pull request'te GitHub Actions'ta (`windows-latest`) da çalışır. Yeni bir davranış eklerken ya da
bir hatayı düzeltirken mümkünse `tests/` altına bir test ekleyin.

## Kod düzeni

- Kullanıcıya görünen her metin Türkçedir. Kod içi yorumlar ve docstring'ler de Türkçe yazılır.
- Python 3.11 ve üstü sözdizimi, tip ipuçları ve `from __future__ import annotations`. Satır uzunluğu
  en fazla 120 karakter.
- Arayüz kuralları [DESIGN.md](DESIGN.md)'dedir. Renk, yazı, boşluk ve köşe değerlerini
  `altyazitr/gui/tokens.py`'den alın; arayüz koduna doğrudan HEX yazmayın. Arayüzde emoji ve Unicode simge
  kullanmayın, `altyazitr/gui/icons.py`'deki ikonları kullanın.
- Varsayılan ayarlarla her şey yerel kalmalı. İnternete çıkan bir özellik isteğe bağlı olmalı ve arayüzde
  bunu açıkça söylemeli.
- Yeni bağımlılık eklerken lisansını kontrol edin. Ticari olmayan lisanslar (ör. CC BY-NC) para kazanılan
  yayınlarla çeliştiği için kabul edilmez. Eklediğiniz bileşeni `THIRD_PARTY_NOTICES.md`'ye yazın.
- Arayüzü görünür biçimde değiştirdiyseniz `python tools/readme_gorselleri.py` ile `docs/gorseller/`'i
  yeniden üretin. Bu araç gerçek ekranı yakalamaz; görseller sahte test sahnesinden gelir.

## Hata bildirmek

[Yeni bir kayıt açıp](../../issues/new/choose) **Hata bildirimi** formunu doldurun. En çok işe yarayanlar:

- Sürüm (*Ayarlar → Uygulama → Sürüm*), kurulum türü ve Windows sürümü
- Oyun ve platform (ör. "GTA VI, PS5, PS Remote Play")
- Okuma yöntemi (ekran bölgesi ya da uygulama penceresi), OCR motoru, çeviri motoru ve modeli
  (ör. Ollama, `gemma4:12b`), oyun profili
- Ne yaptığınız, ne beklediğiniz, ne olduğu
- **Günlük dosyası:** `%LOCALAPPDATA%\RobzVision\Logs` klasöründeki en son dosya. *Ayarlar → Uygulama →
  Günlükler* düğmesi bu klasörü açar.
- Mümkünse yalnızca altyazı bölgesinin ekran görüntüsü

Paylaşmadan önce kontrol edin: Günlükte okunan altyazı metinleri bulunabilir. API anahtarlarınız
`%APPDATA%\RobzVision\ayarlar.json` dosyasındadır; bu dosyayı eklemeyin. Ekran görüntülerinde sohbet, ad,
telefon numarası gibi kişisel bilgiler olmamalı.

## Oyuna özel ipucu önermek

Her oyunun altyazısı farklıdır: yazı tipi, boyut, arka plan, konum. Bir oyunda iyi çalışan ayarları
bulduysanız başkalarının da işine yarar. **Öneri** formunda türü *Oyuna özel ayar veya ipucu* seçin ve
şunları yazın:

- Oyun, platform ve oynama biçimi (PC, uzaktan oynatma, capture kartı)
- Oyun içindeki altyazı ayarları (boyut, arka plan, konuşmacı adı)
- Robz Vision'da işe yarayan ayarlar: okuma yöntemi, bölgenin ekrandaki yeri, OCR motoru, *Parlaklık eşiği*,
  *Algılama*, *Kararlılık*
- Sözlüğe eklediğiniz adlar (karakterler, mekânlar, çeteler)

Yeterince ipucu birikince kullanım kılavuzuna oyun bazında bir bölüm ekleyeceğiz.

## Oyun profili ekleme

Oyun profilleri yapay zekâ çevirisine oyunun dünyasını, çevrilmeyecek adları ve üslubu anlatır. Her profil
`altyazitr/data/oyun_profilleri/` altında bir JSON dosyasıdır. Yeni bir oyun için mevcut bir profili
kopyalayıp düzenleyin:

```json
{
  "id": "ornek_oyun",
  "name": "Örnek Oyun",
  "description": "Vahşi Batı'da geçen açık dünya oyunu",
  "world": "Open-world western set in 1899. Outlaws, sheriffs and ranchers; rough, laconic speech.",
  "rules": ["Keep threats short and hard.", "Gang members address each other with 'sen'."],
  "keep": ["Dusty Creek", "Marshal Hale"],
  "glossary": [["posse", "takip ekibi"], ["bounty", "ödül"]],
  "examples": [["Saddle up, we ride at dawn.", "Atlan, şafakta yola çıkıyoruz."]]
}
```

| Alan | İçerik |
|---|---|
| `id` | Dosya adı, uzantısız: `ornek_oyun.json` → `ornek_oyun` |
| `name`, `description` | Profilin Türkçe adı ve kısa açıklaması |
| `world` | Oyunun dünyası ve tonu; model için İngilizce |
| `rules` | Çeviri kuralları; İngilizce liste |
| `keep` | Çevrilmeyecek adlar: karakter, yer, silah, araç, marka |
| `glossary` | Argo ve terim sözlüğü: `[["en", "tr"], ...]` |
| `examples` | Üslup örnekleri: `[["en", "tr"], ...]` |

- **Kısa tutun.** Profil her istekte modele gider; toplamı yaklaşık 1000 token'ı (kabaca 3–4 bin karakter)
  geçmesin. Uzun profil çeviriyi yavaşlatır.
- **Örnekler özgün olsun.** Oyunun senaryosundan, altyazılarından ya da fragmanlarından cümle almayın
  (telif hakkı). Oyunun üslubunu yansıtan kendi cümlelerinizi yazın.
- Profili Robz Vision'da seçip birkaç sahnede deneyin. Pull request'e mümkünse bir çeviri raporu ekleyin
  (aşağıya bakın).

## Çeviri kalitesini ölçme

`tools/ceviri_testi.py`, `tools/ceviri_testi_seti.json`'daki hazır sahne metinlerini gerçek çeviri motoruyla
çevirir. Her satırın çevirisini ve gecikmesini bir rapora yazar; iki modelin sonucunu yan yana
karşılaştırabilir. Seçenekler için `--help`.

```powershell
.venv\Scripts\python tools\ceviri_testi.py --engine ollama --model gemma4:12b --out rapor.md
```

Oyun profilini, çeviri istemini ya da önerilen modeli değiştiren pull request'lere önce/sonra raporlarını
ekleyin. Test setine eklediğiniz sahneler de özgün olmalı; oyun metninden alıntı yapmayın.

## Sürüm çıkarırken

Etiketi göndermeden önce:

- Sürüm numarasını `altyazitr/__init__.py`, `pyproject.toml`, `installer/RobzVision.iss` (`AppVersion`) ve
  `.github/ISSUE_TEMPLATE/hata.yml`'de güncelleyin. CHANGELOG.md'de *Yayımlanmadı* başlığını sürüm ve tarihle
  değiştirin, alttaki karşılaştırma bağlantılarını güncelleyin.
- **Ollama sabitlerini güncelleyin.** Robz Vision, Ollama'nın kurulum dosyasını sabit bir sürümden indirir;
  boyutunu ve SHA256 özetini koddaki değerlerle karşılaştırır. Son sürümün değerlerini GitHub'ın sürüm
  kaydından okuyun (dosya inmez):

  ```powershell
  gh api repos/ollama/ollama/releases/latest --jq '.tag_name, (.assets[] | [.name, .digest, .size] | @tsv)' | Select-String '^v\d|^OllamaSetup\.exe'
  ```

  İlk satır sürüm etiketidir. `OllamaSetup.exe` satırının ikinci alanı SHA256 özetidir (`sha256:` önekini
  atın), üçüncüsü bayt cinsinden boyuttur. Değerleri şu sabitlere yazın:

  - `altyazitr/ollama_install.py`: `OLLAMA_VERSION` (`"0.40.16"` biçiminde), `SETUP_SHA256`, `SETUP_BYTES`.
    `SETUP_URL` sürümden oluşur; GitHub'daki adres biçimi değişmediyse dokunmayın.
  - `altyazitr/ai_setup.py`'deki kopyalar: `OLLAMA_VERSION` (`(0, 40, 16)` biçiminde) ve `SETUP_BYTES`.
  - `altyazitr/gui/ai_setup_view.py`'deki yedek değerler: `_SETUP_VERSION` ve `_SETUP_BYTES`.
  - Testlerdeki kopyalar: `tests/test_ai_setup.py` (`NEW`, `INST_NEW`) ve `tests/test_setup_dialog.py`
    (`SETUP`).

  Testleri çalıştırın ve yeni Ollama sürümünü temiz bir bilgisayarda, ilk açılıştaki yapay zekâ kurulumuyla
  baştan sona deneyin.
- `v<sürüm>` etiketini gönderin; GitHub Actions derleyip Releases'e yükler.

## Pull request

- Bir pull request tek bir konuyu çözsün. Büyük değişikliklerden önce bir kayıt açıp konuşalım.
- Testler geçmeli. Arayüz değişikliklerinde önce/sonra ekran görüntüsü ekleyin
  (`python tools/arayuz_goruntusu.py ÇIKTI_KLASÖRÜ` pencere açmadan tüm ekranları kaydeder).
- Davranış değiştiyse README.md, docs/KULLANIM.md ve CHANGELOG.md'yi güncelleyin.

Katkılarınız projenin [MIT lisansı](LICENSE) altında yayımlanır.
