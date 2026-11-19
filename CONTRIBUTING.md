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
  yeniden üretin. Bu araç gerçek ekranı yakalamaz; görseller sahte test sahnesinden gelir. README'deki GTA VI
  zeminli görseller (`gta6_*`) yalnız resmî tanıtım görsellerinin klasörü `--kaynak KLASÖR` ile verilince üretilir;
  o kaynak görselleri depoya eklemeyin.

## Hata bildirmek

[Yeni bir kayıt açıp](../../issues/new/choose) **Hata bildirimi** formunu doldurun. En çok işe yarayanlar:

- Sürüm (*Ayarlar → Uygulama → Sürüm*), kurulum türü ve Windows sürümü
- Oyun ve platform (ör. "GTA VI, PS5, PS Remote Play")
- Okuma yöntemi (ekran bölgesi ya da uygulama penceresi), OCR motoru, çeviri motoru ve modeli
  (ör. Ollama, `gemma4:12b`)
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

## Çeviri kalitesini ölçme

`tools/ceviri_testi.py`, `tools/ceviri_testi_seti.json`'daki hazır sahne metinlerini gerçek çeviri motoruyla
çevirir. Her satırın çevirisini ve gecikmesini bir rapora yazar; iki modelin sonucunu yan yana
karşılaştırabilir. Seçenekler için `--help`.

```powershell
.venv\Scripts\python tools\ceviri_testi.py --engine ollama --model gemma4:12b --out rapor.md
```

Çeviri kurallarını (`altyazitr/data/oyun_profilleri/genel.json`), çeviri istemini ya da önerilen modeli
değiştiren pull request'lere önce/sonra raporlarını
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
- **Uygulama içinden güncelleme şunlara dayanır** (`altyazitr/updater.py`; `.github/workflows/release.yml`
  üretir). Değiştirirseniz kullanıcıların uygulaması kendini güncelleyemez; `tests/test_updater.py` denetler:
  - Etiket `vX.Y.Z` biçiminde ve sürüm GitHub'da *Latest* olmalı (`make_latest: true`). Taslak ve ön sürümler
    (`-beta`, *Pre-release*) yok sayılır.
  - Kurulum dosyasının adı `RobzVision-Setup-X.Y.Z-x64.exe` (`installer/RobzVision.iss`: `OutputBaseFilename`),
    özet dosyası `SHA256SUMS.txt` (`<sha256>  <dosya>` satırları). Dosyalar sürümün kendi indirme adresinde
    olmalı: `https://github.com/kutsalkiremitci/robz-vision/releases/download/vX.Y.Z/…`. Depoyu yeniden
    adlandırmayın ya da taşımayın: başka adresteki dosyayı uygulama indirmez.
  - `RobzVision.iss`'teki `AppId`, `AppMutex` ve `[Code]` bölümü (`/GUNCELLEME`, `/BEKLE=`): uygulama kurulumu
    sessiz başlatıp kapanır, kurulum uygulamanın kapanmasını bekler ve bitince onu yeniden açar.
  - Yayımlanmış bir sürümün dosyalarını sonradan değiştirmeyin: uygulama indirdiği dosyayı `SHA256SUMS.txt`'deki ve
    GitHub'ın verdiği özetle karşılaştırır; tutmazsa kurmaz.

  Yeni sürümde güncellemeyi bir önceki sürümden deneyin: eski sürümü kurun, *Ayarlar → Uygulama → Şimdi
  denetle*, **Güncelle**; uygulama kapanıp yeni sürümle açılmalı ve ayarlar aynı kalmalı.
- `v<sürüm>` etiketini gönderin; GitHub Actions derleyip Releases'e yükler.

## Pull request

- Bir pull request tek bir konuyu çözsün. Büyük değişikliklerden önce bir kayıt açıp konuşalım.
- Testler geçmeli. Arayüz değişikliklerinde önce/sonra ekran görüntüsü ekleyin
  (`python tools/arayuz_goruntusu.py ÇIKTI_KLASÖRÜ` pencere açmadan tüm ekranları kaydeder).
- Davranış değiştiyse README.md, docs/KULLANIM.md ve CHANGELOG.md'yi güncelleyin.

Katkılarınız projenin [MIT lisansı](LICENSE) altında yayımlanır.
