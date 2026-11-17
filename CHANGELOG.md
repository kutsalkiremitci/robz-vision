# Değişiklik geçmişi

Bu projedeki önemli değişiklikler bu dosyada tutulur. Biçim
[Keep a Changelog](https://keepachangelog.com/tr-TR/1.1.0/)'a, sürüm numaraları
[Anlamsal Sürümleme](https://semver.org/lang/tr/)'ye dayanır.

## [Yayımlanmadı]

### Düzeltildi

- Yazı artık rengine bakılmadan algılanıyor ve okunuyor. Önceden yalnızca beyaz ve sarı yazı tanınıyordu:
  "Follow the **Tennis Coach**" gibi mavi, yeşil, kırmızı, mor vurgulu kelimeler düşüyor, tamamı renkli
  satırlar hiç fark edilmiyordu.
- Altyazı algılama koyu kenarlı/koyu zemindeki yazıya bakıyor: araba giderken akan asfalt, yol çizgisi ve
  lambalar artık "yazı var" ya da "yazı değişti" sayılmıyor. Böylece yazı belirince beklemeden okunuyor ve
  kaybolunca ekrandan siliniyor. Önizlemedeki yeşil alan da yalnızca yazıyı gösteriyor.

## [1.0.1] - 2026-11-17

### Düzeltildi

- Kumlu ya da hareketli arka planda (asfalt, çimen, sarı yol çizgisi) altyazı yanlış okunuyor, kelimeler
  kayboluyor ve çeviri bozuluyordu. Yeni "kenarlı yazıyı ayır" ön işlemesi, koyu kenarlı ya da gölgeli
  altyazıyı arka plandaki parlak tanelerden ayırır. "Otomatik" mod bu yöntemi ve parlak metin ayırmayı birlikte
  dener, daha çok anlamlı İngilizce kelime okuyanı seçer.

### Eklendi

- Ayarlar → Metin okuma → Gelişmiş → Ön işleme listesine "Kenarlı yazıyı ayır" seçeneği.

### Değişti

- Sürüm dosyalarının adları: `RobzVision-Setup-<sürüm>-x64.exe`, `RobzVision-Portable-<sürüm>-x64.zip`
  ve `.7z`.

## [1.0.0] - 2026-11-16

İlk genel sürüm.

### Eklendi

- Ekran bölgesinden ya da belirli bir uygulama penceresinden İngilizce altyazı okuma. Pencere modu, pencere
  başka pencerelerin arkasında kalsa da çalışır (Windows Graphics Capture).
- Değişim algılama: altyazı değişmedikçe OCR çalışmaz; harf harf beliren altyazılar sabitlenince okunur.
- İki OCR motoru: Windows OCR (varsayılan) ve RapidOCR (PP-OCR, ONNX).
- Okuma temizliği: satır birleştirme, konuşmacı adını ayırma, İngilizce kelime listesiyle kalite puanı,
  titreyen okumaları tekilleştirme ve daha temiz okuma gelince çeviriyi düzeltme.
- Yerel ve ücretsiz çeviri: quickmt-en-tr modeli (CTranslate2, CPU). Model ilk açılışta bir kez indirilir
  (yaklaşık 400 MB).
- İsteğe bağlı çeviri motorları: Ollama (TranslateGemma, Gemma 4, Qwen 3.5, Hy-MT2), Google Gemini ve
  DeepL API Free. Hibrit mod önce yerel çeviriyi gösterir; hata ya da kota sınırında yerel çeviriyle devam
  eder.
- Sözlük, bağlam satırları, oyun bağlamı notu ve diske kaydedilen çeviri önbelleği.
- Ekranda taşınabilir, boyutlandırılabilir altyazı kutusu. Varsayılan olarak ekran yakalamaya girmez;
  "Yayında da görünsün" ile OBS Ekran Yakalama'da görünür.
- OBS Tarayıcı Kaynağı için yalnızca `127.0.0.1`'i dinleyen yerel sunucu ve WebSocket ile anlık güncelleme.
  İsteğe bağlı olarak OBS'te İngilizce altyazının üstünü kapatma.
- Görünüm sayfası: canlı önizleme, hazır stiller (Klasik, Sinema, Kutulu, Sarı, Minimal), yazı tipi, boyut,
  renk, kenar, gölge, arka plan, konuşmacı rengi ve İngilizce orijinali gösterme.
- "Ocean Drive gecesi" tasarım sistemine dayanan PySide6 arayüzü, ilk açılış tanıtım turu, global kısayollar
  (`Ctrl+Alt+T`, `Ctrl+Alt+H`, `Ctrl+Alt+C`) ve sistem tepsisi.
- Yönetici izni istemeyen Inno Setup kurulum sihirbazı, taşınabilir zip ve 7z arşivi, SHA256 özetleri.
  Kurulum dosyası ARM işlemcili Windows 11'e de kurulur (x64 öykünmesi). Etiket gönderildiğinde GitHub
  Actions ile derleme ve Releases'e yükleme.

[Yayımlanmadı]: ../../compare/v1.0.1...HEAD
[1.0.1]: ../../releases/tag/v1.0.1
[1.0.0]: ../../releases/tag/v1.0.0
