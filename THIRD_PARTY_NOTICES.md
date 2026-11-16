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
| PaddleOCR PP-OCR modelleri | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR (RapidOCR ilk kullanımda indirir) |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| OpenCV | Apache-2.0 | https://opencv.org |
| NumPy | BSD-3-Clause | https://numpy.org |
| aiohttp | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| requests | Apache-2.0 | https://github.com/psf/requests |
| RapidFuzz | MIT | https://github.com/rapidfuzz/RapidFuzz |
| mss | MIT | https://github.com/BoboTiG/python-mss |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| huggingface_hub | Apache-2.0 | https://github.com/huggingface/huggingface_hub |

## Programla birlikte dağıtılmayan, isteğe bağlı hizmetler

- **Ollama** ve indirdiğiniz LLM modelleri ayrı kurulan bağımsız yazılımlardır; her modelin kendi
  lisansı geçerlidir (ör. TranslateGemma: Gemma Kullanım Şartları; Gemma 4 / Qwen 3.5 / Hy-MT2: Apache-2.0).
- **Google Gemini API** ve **DeepL API** üçüncü taraf çevrimiçi hizmetlerdir; kullanımları ilgili
  hizmetin şartlarına tabidir. Gemini'nin ücretsiz katmanında gönderilen metinler Google tarafından
  ürün geliştirme amacıyla kullanılabilir.
- **NLLB-200** gibi ticari olmayan (CC BY-NC) lisanslı modeller bilinçli olarak kullanılmamıştır;
  para kazanılan yayınlarda kullanımı lisansa aykırı olabilir.

Robz Vision; Rockstar Games, Take-Two, Sony Interactive Entertainment, OBS Project,
Kick, Google veya DeepL ile bağlantılı değildir. Adı geçen markalar sahiplerine aittir.
