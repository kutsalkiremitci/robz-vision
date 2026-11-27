# Üçüncü taraf bileşenler ve lisansları

Robz Vision'ın kendi kaynak kodu MIT lisanslıdır (bkz. `LICENSE`). Program aşağıdaki açık kaynak
bileşenleri kullanır. Paketlenmiş (derlenmiş) sürümde bu bileşenlerin ikili dosyaları ayrı dosyalar
olarak yer alır ve her biri kendi lisansına tabidir.

| Bileşen | Lisans | Not |
|---|---|---|
| Qt 6 / PySide6 / Shiboken6 | LGPL-3.0 | Qt kütüphaneleri ayrı DLL dosyaları olarak dağıtılır ve değiştirilebilir. Kaynak: https://code.qt.io , https://download.qt.io/official_releases/QtForPython/ |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| SentencePiece | Apache-2.0 | https://github.com/google/sentencepiece |
| quickmt-en-tr çeviri modeli | CC BY 4.0 | © quickmt — https://huggingface.co/quickmt/quickmt-en-tr . Model programla birlikte dağıtılmaz; ilk kullanımda indirilir. |
| `altyazitr/data/en_words.txt` | CC BY 4.0 | quickmt-en-tr kaynak sözlüğünden türetilmiş İngilizce kelime listesi (yalnızca OCR kalite puanı için). |
| windows-capture | MIT | https://github.com/NiiightmareXD/windows-capture |
| pywinrt (winrt-*) | MIT | https://github.com/pywinrt/pywinrt |
| RapidOCR | Apache-2.0 | https://github.com/RapidAI/RapidOCR |
| PaddleOCR PP-OCR modelleri | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR (programla birlikte gelir) |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| OpenCV | Apache-2.0 | https://opencv.org |
| NumPy | BSD-3-Clause | https://numpy.org |
| aiohttp | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| requests | Apache-2.0 | https://github.com/psf/requests |
| RapidFuzz | MIT | https://github.com/rapidfuzz/RapidFuzz |
| mss | MIT | https://github.com/BoboTiG/python-mss |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| huggingface_hub | Apache-2.0 | https://github.com/huggingface/huggingface_hub |

## Programla birlikte dağıtılmayan, isteğe bağlı bileşenler ve hizmetler

Aşağıdakiler Robz Vision'la birlikte dağıtılmaz. Ollama'yı ve Gemma 4'ü, yerel yapay zekâ kurulumunu
onaylarsanız Robz Vision resmî kaynaklarından kurar (ayrıntı tabloda); ötekileri kullanmak isterseniz kendiniz
kurar ya da indirirsiniz. Her biri kendi lisansına ve şartlarına tabidir.

| Bileşen | Lisans | Not |
|---|---|---|
| Ollama | MIT | © Ollama — https://github.com/ollama/ollama . Programla dağıtılmaz. Yerel yapay zekâ kurulumunu onaylarsanız Robz Vision, Ollama'nın resmî ve imzalı Windows kurulum dosyasını (`OllamaSetup.exe`, sabit sürüm) GitHub'daki sürüm sayfasından indirir, SHA256 özetini ve imzasını denetleyip kullanıcı hesabınıza kurar. Ollama ayrı bir program olarak kalır ve kendi kaldırıcısıyla kaldırılır; Robz Vision ona yerel adresinden (varsayılan `http://127.0.0.1:11434`) bağlanır. |
| Gemma 4 dil modelleri (`gemma4:12b`, `gemma4:e4b`, `gemma4:26b`) | Apache-2.0 | Google DeepMind — https://gemma.ai/models/gemma-4/ , lisans: https://gemma.ai/resources/apache-2/ . Programla dağıtılmaz. Önerilen yerel yapay zekâ modeli; kurulumu onaylarsanız Ollama, kendi kayıt sunucusundan (`registry.ollama.ai`) indirir: https://ollama.com/library/gemma4 |

- Ollama'dan indirdiğiniz diğer modellerde model sayfasındaki lisans koşulları geçerlidir. Örneğin
  TranslateGemma, Gemma 4'ten farklı olarak Gemma Kullanım Şartları'na tabidir:
  https://gemma.ai/resources/terms/
- **Google Gemini API** ve **DeepL API** üçüncü taraf çevrimiçi hizmetlerdir; kullanımları ilgili
  hizmetin şartlarına tabidir. Gemini'nin ücretsiz katmanında gönderilen metinler Google tarafından
  ürün geliştirme amacıyla kullanılabilir.
- **NLLB-200** gibi ticari olmayan (CC BY-NC) lisanslı modeller bilinçli olarak kullanılmamıştır;
  para kazanılan yayınlarda kullanımı lisansa aykırı olabilir.

Oyun profillerinde (`altyazitr/data/oyun_profilleri/`) geçen oyun, karakter, yer, silah, araç ve marka
adları yalnızca çeviride korunsun diye listelenir; sahiplerine aittir. Profillerdeki üslup örnekleri projeye
özgüdür, oyun metninden alıntı değildir.

Robz Vision; Rockstar Games, Take-Two, Sony Interactive Entertainment, OBS Project,
Kick, Google, Ollama veya DeepL ile bağlantılı değildir. Adı geçen markalar sahiplerine aittir.

**Kick markası:** Kick adı ve logosu (`altyazitr/data/kick.svg`, yeşil "K" işareti) Kick'e aittir. Uygulamada,
README'de ve web sitesinde yalnız geliştiricinin Kick kanalına (https://kick.com/robzsogood) bağlantı vermek için,
Kick'in marka yönergelerine uygun olarak, oranı ve rengi değiştirilmeden kullanılır. README'deki Kick düğmesinde
(shields.io rozeti; logo Simple Icons'tan) ve tanıtım görselinde logo, koyu bir zemin üzerinde Kick yeşiliyle,
değiştirilmeden durur. Logo, Robz Vision'ın MIT lisansının kapsamında
değildir. Robz Vision'ın geliştiricisi Kick'te yayın yapar; uygulama Kick'in bir ürünü değildir ve Kick tarafından
desteklenmez.

**Discord markası:** Discord adı ve logosu Discord Inc.'e aittir. README'deki Discord düğmesinde (shields.io
rozeti; logo Simple Icons'tan) yalnız Robz so good'un Discord sunucusuna bağlantı vermek için kullanılır.

**Oyun görselleri:** README'deki, kullanım kılavuzundaki ve web sitesindeki örnek görsellerin
(`docs/gorseller/gta6_*`, `site/img/gta6_*`, `site/img/og.jpg`) zemini Grand Theft Auto VI'nın resmî tanıtım
görselleridir. Oyun görseli © Rockstar Games / Take-Two Interactive. Robz Vision bağımsız bir hayran projesidir,
Rockstar Games ile bağlantılı değildir ve onlarca desteklenmez. Görseller yalnız uygulamanın ne yaptığını
göstermek için kullanılır ve Robz Vision'ın MIT lisansının kapsamında değildir.
Üzerlerindeki İngilizce altyazılar ve Türkçe çeviriler projeye özgü örneklerdir, oyunun metninden alıntı
değildir; Rockstar Games'in logoları kullanılmaz. Tam boy kaynak görseller depoda yoktur;
`tools/readme_gorselleri.py` onları yalnız `--kaynak` ile verilen klasörden okur.

Uygulamadaki *Görünüm* sayfasının önizleme zemini (`altyazitr/data/sahne_gunduz.jpg`, `sahne_gece.jpg`) de
aynı resmî tanıtım görsellerinden ikisinin 1280×720'ye küçültülmüş kopyasıdır ve programla birlikte gelir;
Türkçe altyazının oyun üstünde nasıl görüneceğini göstermek için kullanılır. Oyun görseli © Rockstar Games /
Take-Two Interactive; MIT lisansının kapsamında değildir. Dosyalar silinirse önizleme çizilmiş bir sahneye döner.
