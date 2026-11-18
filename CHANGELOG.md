# Değişiklik geçmişi

Bu projedeki önemli değişiklikler bu dosyada tutulur. Biçim
[Keep a Changelog](https://keepachangelog.com/tr-TR/1.1.0/)'a, sürüm numaraları
[Anlamsal Sürümleme](https://semver.org/lang/tr/)'ye dayanır.

## [Yayımlanmadı]

## [1.2.1] - 2026-11-18

### Eklendi

- "Oyun profili"nin yanında bilgi simgesi: üzerine gelince profilin ne işe yaradığı görünür (oyunun dünyası
  ve tonu, çevrilmeyecek adlar, argo sözlüğü, üslup); başka bir oyun oynarken *Genel*'i seçin.

### Değişti

- Çeviri sayfasının Ollama bölümünde seçili model zaten yüklüyse **Modeli indir** düğmesi görünmez. Yüklü
  modeller arka planda okunur; yenile düğmesi artık arayüzü dondurmuyor.

## [1.2.0] - 2026-11-18

### Eklendi

- **İlk açılışta "Çeviri nasıl yapılsın?" seçimi:** *Yapay zekâ ile* ya da *Yalnız hızlı çeviri*. Robz
  Vision ekran kartını okuyup öneri yapar ve kartın adını gösterir: uygun kartta *Yapay zekâ ile* önceden
  seçili gelir; 12 GB'lık kartta Gemma 4 12B, 8 GB'lık kartta daha hafif Gemma 4 E4B önerilir. 6 GB'lık kartta
  seçilebilir ama uyarı çıkar; uygun kart yoksa seçenek kapalıdır ve nedeni yazar. Seçim sonra Çeviri
  sayfasından değişir.
- **Yapay zekâyı uygulamanın kendisi kurar.** Ollama'nın resmî ve imzalı kurulum dosyası GitHub'daki sürüm
  sayfasından iner; sürüm sabittir (0.40.16). Dosya çalıştırılmadan önce boyutu, SHA256 özeti ve dijital
  imzası (Ollama Inc.) denetlenir; tutmazsa silinir, kurulmaz. Kurulum sessizdir, yönetici izni gerekmez.
  Ardından Gemma 4 modeli iner. Toplam yaklaşık 10 GB (Ollama 1,9 GB + Gemma 4 12B 8,0 GB); bunlardan hiçbiri
  **Kur**'a basılmadan inmez. Ollama ekran kartını kullanamıyorsa model indirilmez. Ollama ve model zaten
  kuruluysa yalnız ayar yapılır.
- Kurulumun her adımı (indirme, imza ve bütünlük denetimi, kurulum, başlatma, ekran kartı denetimi, model
  indirme) kendi satırında görünür; altta ilerleme çubuğu, indirme hızı ve kalan süre.
- **Arka planda iner:** hızlı çeviri modeli inince **Şimdi başla** ile programı açarsınız; yapay zekâ arka
  planda iner, hazır olunca devreye girer. Çeviri çalışırken indirme durur (yayın bağlantısı ve Wi-Fi'deki
  Remote Play görüntüsü bozulmasın), durdurunca kaldığı yerden sürer; **Yine de sürdür** ile durmaz. Program
  yarıda kapanırsa iş sonraki açılışta kaldığı yerden sürer.
- Kenar çubuğunda yapay zekâ durum satırı: ilerleme, duraklama ya da hata; tıklayınca Çeviri sayfası açılır.
  Kurulum bitince ya da yapılamayınca bildirim çıkar.

### Değişti

- Çeviri sayfasındaki **Önerilen kurulum** artık her şeyi kurar: Ollama yoksa onu da indirip kurar, sonra modeli
  indirir ve motoru ayarlar. Ollama'nın sitesine yönlendirme yok; önce indirilecekleri, boyutlarını ve boş disk
  alanını gösteren onay penceresi açılır.
- Ollama kapalıysa Robz Vision onu arka planda kendisi başlatır.
- Yapay zekâ hazırlanamazsa (Ollama başlatılamadı ya da model indirilmemiş) her Başlat'ta çıkan hata penceresi
  yerine bildirim çıkar; çeviri hızlı yerel çeviriyle sürer.
- Yerel Ollama istekleri sistemin vekil sunucusunu (proxy) kullanmaz: vekil sunucu `127.0.0.1` isteklerini
  yanlış yere götürebiliyordu.
- 1.1.0 ve öncesinden güncelleyenlerde ilk açılış penceresi açılmaz, hiçbir şey kendiliğinden inmez. Motoru
  hızlı çeviri olan ve ekran kartı uygun olan kullanıcıya bir kez "Yeni: yapay zekâ çevirisi. Çeviri sayfasından
  kurabilirsiniz." bildirimi çıkar.

## [1.1.0] - 2026-11-18

### Eklendi

- **Vurgulu kelimeler:** oyun görev yazısında bir yeri, kişiyi ya da eşyayı renkli yazarsa (GTA 6'da pembe,
  eskilerde mavi, sarı, yeşil) çeviride de karşılığı renkli görünür: "Drive to the **radar station**" →
  "**Radar istasyonuna** sür". Ekranınızdaki kutuda da OBS'te de. Renk Görünüm sayfasındaki *Vurgu rengi* ile
  değişir (varsayılan GTA 6'nın pembesi); *Vurgulu kelimeler* ile kapatılır. Renkli kelimeyi en iyi yerel yapay
  zekâ ve Gemini taşır; hızlı yerel çeviri en iyi çabayla bulur, DeepL vurgusuz çevirir.

- **Yerel yapay zekâ çevirisi (önerilen):** Ollama ile Gemma 4 (`gemma4:12b`). Ücretsiz, internetsiz ve
  kotasız çalışır. Önceki 8 satırı görür, konuşmanın gidişatına göre çevirir; OCR'ın yanlış okuduğu harfleri
  düzeltir, tuş ipuçlarını ("Hold L2 to …") ayırır. Yaklaşık 8 GB indirilir; 12 GB ekran kartında satır başına
  ~1 sn sürer. Daha zayıf kartlar için `gemma4:e4b`. [Ollama](https://ollama.com/download) kurulduktan sonra
  Çeviri sayfasının Ollama bölümündeki **Önerilen kurulum** düğmesi modeli indirir, motoru ayarlar ve GTA 6
  profilini seçer.
- **Oyun profilleri:** yapay zekâya oyunun dünyası ve tonu, çevrilmeyecek adlar (karakter, yer, silah, araç,
  marka), suç dünyası argo sözlüğü ve üslup örnekleri verilir. Çeviriler GTA 6'nın suç dünyasına uyar,
  kelimesi kelimesine saçma çeviriler azalır. Varsayılan profil "GTA 6"; diğer oyunlar için "Genel".
  *Ek bağlam* kutusu kendi notunuzu profile ekler. Profildeki adlar her motorda, hızlı yerel çeviride de
  çevrilmeden kalır. Profiller `altyazitr/data/oyun_profilleri/` altındaki JSON dosyalarıdır; yeni oyun
  profili eklemek için CONTRIBUTING.md'ye bakın.
- Katkıcılar için `tools/ceviri_testi.py`: hazır sahne metinlerini (`tools/ceviri_testi_seti.json`) gerçek
  çeviri motoruyla çevirir, çeviri ve gecikme raporu üretir, iki modelin sonucunu yan yana karşılaştırır.

### Değişti

- **Hibrit mod:** yapay zekânın çevirisi ~1,2 sn içinde gelirse yalnız o görünür; gecikirse önce hızlı yerel
  çeviri (quickmt) görünür, yapay zekânınki gelince üstüne yazılır. Önceden her satırda önce hızlı çeviri
  görünüyor, ardından değişiyordu. Ayar: Çeviri sayfasında "Yapay zekâ gecikirse hızlı çeviriyi göster".
- Ollama'nın varsayılan modeli TranslateGemma (`translategemma:4b`) yerine Gemma 4 (`gemma4:12b`);
  TranslateGemma bağlamı ve oyun profilini kullanmadığı için artık önerilmiyor. Bağlam 3 satırdan 8 satıra
  çıktı. Bu iki ayarı değiştirmemiş kullanıcılarda yeni değerlere geçilir; kendi seçtiğiniz model ve satır
  sayısı korunur. Yeni model indirilene kadar hızlı yerel çeviri kullanılır.
- *Oyun bağlamı* alanının yerini *Ek bağlam* aldı; yazdığınız not korunur ve profile eklenir.
- Gemini'de önerilen model Gemini 3.5 Flash-Lite: daha güçlü Flash modellerinin ücretsiz kotası canlı yayında
  birkaç dakikada doluyor. Ücretsiz katman sınırları Google AI Studio'da görülür.

### Düzeltildi

- Oyunda sabit duran bir yazı (tuş ipucu, menü) son çeviriyi ekranda sonsuza dek tutabiliyordu. Artık her
  satır en fazla *En uzun kalma* süresi kadar görünür (varsayılan 7 sn; uzun satırlara okuma süresi kadar
  pay). Süresi dolup kalkan satır, yazı oyunda dursa da geri gelmez. Ayar:
  *Ayarlar → Altyazı akışı → En uzun kalma* (3–20 sn).

## [1.0.4] - 2026-11-17

### Eklendi

- **Altyazı akışı:** son çeviriler alt alta kalır (en yenisi altta, öncekiler yukarıda soluk), her satır oyun
  sonrakine geçse de en az 3 saniye ekranda durur. Ekranda ve OBS'te aynı çalışır; *Ayarlar → Altyazı akışı*'ndan
  satır sayısı (2–4), en az gösterim süresi ya da eski "Tek satır" seçilebilir.
- **Son çeviriler:** Ana sayfada oturumdaki çevirilerin saatli listesi (Türkçe + İngilizce); tek satır ya da
  tümü kopyalanabilir. Yalnızca bellekte tutulur, diske yazılmaz.

### Düzeltildi

- Hızlı konuşmada altyazı satırları kayboluyordu: OCR ya da çeviri meşgulken gelen yeni satır, bekleyenin
  üzerine yazılıyordu. Aşamalar arası artık sıralı kuyruk var; her satır okunup çevriliyor. Hibrit modda yavaş
  motor (Ollama/Gemini) yeni satırların hızlı çevirisini bekletmiyor. Benzetimde kaybolan satır: yavaş motorla
  8 → 0, art arda 7 kısa satırda 3 → 0.
- Ekrandaki kutunun varsayılan yüksekliği, akıştaki üç satır sığacak şekilde büyütüldü.
- Açık zemindeki beyaz altyazılar (GTA V'te kaldırım, beton, gökyüzü üstündeki gölgeli ya da gölgesiz yazı)
  "Metin yok" sayılıyor ve hiç okunmuyordu. Algılamaya yapay zekâ metin bulucu (PP-OCRv6, programla
  birlikte gelir) eklendi ve varsayılan olarak açık: renk ve kenar kuralına bakmadan harf şeklini tanır,
  saniyede 5 kez çalışır. Ölçümde açık zeminde kaçırılan altyazı 16/16 → 0/16; boş karede yanlış alarm yok.
  Yaklaşık %10 ek işlemci kullanır; *Ayarlar → Metin okuma → Gelişmiş*'ten kapatılabilir.

## [1.0.3] - 2026-11-17

### Değişti

- Varsayılan OCR motoru artık RapidOCR (PP-OCRv6). Kareyi hem ham hâliyle hem asfalt/kum ayıklanmış hâliyle
  okuyup daha iyisini seçiyor; ölçümde gerçek GTA karesinde %96 → %100, süslü yazı tiplerinde ve renkli
  vurgularda %100. Windows'un İngilizce OCR dil paketi artık gerekmiyor (yalnızca Windows OCR seçilirse).
  Eski ayarlarda varsayılan Windows OCR bir kez RapidOCR'a çevrilir.
- RapidOCR modelleri programla birlikte geliyor; ilk okumada internetten indirme yapılmıyor.

### Düzeltildi

- Okumalar arasında seçim yaparken arka plandaki plaka/tabela yazısı ("LAMARG" gibi) artık ham okumayı öne
  geçirmiyor: yalnızca sözlükte bilinen kelimeler sayılıyor.

## [1.0.2] - 2026-11-17

### Eklendi

- Görünüm sayfasında **Ekranımda | Yayınımda** seçici: oyunun üstündeki kutu ile OBS'teki altyazı ayrı ayarlanır
  (yazı tipi, boyut, renk, kenar, arka plan…). Örneğin ekranda küçük yazı, yayında kutulu ve büyük. Hazır
  stiller ve "Varsayılana dön" yalnızca seçili tarafı değiştirir; diğer taraftaki görünüm tek tıkla kopyalanabilir.
  Eski ayarlarda iki taraf da mevcut görünümle başlar.

### Düzeltildi

- Yazı artık rengine bakılmadan algılanıyor ve okunuyor. Önceden yalnızca beyaz ve sarı yazı tanınıyordu:
  "Follow the **Tennis Coach**" gibi mavi, yeşil, kırmızı, mor vurgulu kelimeler düşüyor, tamamı renkli
  satırlar hiç fark edilmiyordu.
- Altyazı algılama koyu kenarlı/koyu zemindeki yazıya bakıyor: araba giderken akan asfalt, yol çizgisi ve
  lambalar artık "yazı var" ya da "yazı değişti" sayılmıyor. Böylece yazı belirince beklemeden okunuyor ve
  kaybolunca ekrandan siliniyor. Önizlemedeki yeşil alan da yalnızca yazıyı gösteriyor.
- OBS, Robz Vision'dan önce açılınca tarayıcı kaynağı boş kalıyor ve altyazı (Test dahil) OBS'te hiç
  çıkmıyordu; OBS yüklenemeyen sayfayı yeniden denemiyor. OBS artık sayfanın kopyasını saklıyor ve uygulama
  açılınca kendiliğinden bağlanıyor (kaynağın bir kez yenilenmesi gerekir).
- "OBS bağlı" durumu, adres bir web tarayıcısında açıkken de "bağlı" gösteriyordu. Artık yalnızca OBS'teki
  kaynak sayılıyor; bağlı değilken ipucu kaynağın nasıl yenileneceğini söylüyor.

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

[Yayımlanmadı]: ../../compare/v1.2.1...HEAD
[1.2.1]: ../../releases/tag/v1.2.1
[1.2.0]: ../../releases/tag/v1.2.0
[1.1.0]: ../../releases/tag/v1.1.0
[1.0.4]: ../../releases/tag/v1.0.4
[1.0.3]: ../../releases/tag/v1.0.3
[1.0.2]: ../../releases/tag/v1.0.2
[1.0.1]: ../../releases/tag/v1.0.1
[1.0.0]: ../../releases/tag/v1.0.0
