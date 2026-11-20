# Değişiklik geçmişi

Bu projedeki önemli değişiklikler bu dosyada tutulur. Biçim
[Keep a Changelog](https://keepachangelog.com/tr-TR/1.1.0/)'a, sürüm numaraları
[Anlamsal Sürümleme](https://semver.org/lang/tr/)'ye dayanır.

## [Yayımlanmadı]

## [1.4.1] - 2026-11-20

### Düzeltildi

- **Windows kullanıcı adında Türkçe harf (ö, ü, ş, ç, ğ, ı) olunca çeviri başlamıyordu.** Hızlı çeviri modeli
  indirilmiş olsa da "Çeviri motoru hazırlanamadı: NOT_FOUND … src.spm.model: No such file or directory" hatası
  çıkıyordu. Sebep, modelin sözcük bölücüsünün (SentencePiece) Windows'ta ASCII dışı harfli klasör yolunu
  açamaması. Bölücü modeli artık dosyanın içeriğinden yüklenir; yol ne olursa olsun çalışır. Modeli yeniden indirmek
  gerekmez ([#3](https://github.com/kutsalkiremitci/robz-vision/issues/3)).

## [1.4.0] - 2026-11-20

### Eklendi

- **Ana sayfada canlı kaynak önizlemesi (OBS önizlemesi gibi).** Ekranın ya da seçilen pencerenin tamamı canlı
  görünür, okunan bölge üstünde çerçeveyle işaretlidir. Bölge doğrudan önizlemede çizilir, ortasından taşınır,
  köşelerinden boyutlandırılır (ok tuşları da çalışır); değişiklik hemen, çeviri sürerken de geçerli olur.
  Tam ekran seçici *Tam ekranda seç* olarak kaldı. Birden çok ekran varsa önizlenen ekran seçilir.
- Ana sayfa OBS düzeninde: üstte ince komut çubuğu (Başlat, durum, son satırın OCR ve çeviri süreleri; her zaman
  görünür), sağda büyük canlı önizleme ve altında yan yana Ekranımda / OBS panelleri, solda boydan boya
  *Son çeviriler* akışı. Dar pencerede hepsi alt alta, önizleme en üstte. Pencere 1080p ekranda 1280×840 açılır
  (önce 980×760). *Tam ekranda seç* önizlemenin başlığında; uyarılar (ör. pencere simge durumunda) önizlemenin
  içinde görünür.
- Çeviri sürerken okunan bölge (metin sanılan yerler yeşil) önizlemenin altında görünür.
- *Uygulama penceresi* listesi kendiliğinden güncellenir: liste her açılışta ve pencere modunda iki saniyede bir
  (yeni açılan, kapanan pencereler; seçim korunur). Seçili pencere sonradan açılınca önizleme ona geçer.
- Varsayılan altyazı bölgesi GTA VI'nın altyazı şeridi (altta orta; x %17,6, y %80,4, genişlik %64,2, yükseklik
  %19,6). Yeni kullanıcıda ekran modunda bölge seçmeden başlanabilir: açılışta birincil ekrana atanır, önizlemede
  kaydırılır. Pencere modunun varsayılanı da aynı. Seçilmiş bölgesi olan kullanıcıya dokunulmaz.
- **Oyun altyazısının üzerine: üst üste yazılara ayrı kutular.** Oyun bölgede aynı anda birden çok ayrı yazı
  gösterdiğinde (ör. altta konuşma, onun üstünde daha küçük görev yazısı) her biri ayrı okunur, ayrı çevrilir ve
  her birinin üstüne kendi koyu kutusu, kendi yerinde ve kendi yazı boyunda aynı anda yerleşir; ekranınızda da
  OBS'te de. Bölgede en çok 3 yazı izlenir (fazlası varsa en alttakiler). Yazılardan biri kaybolunca yalnız onun
  kutusu kalkar, öbürü yerinde kalır. Yapay zekânın gördüğü önceki satırlara yalnız asıl yazı (en büyük yazılı
  olan, genellikle konuşma) girer; görev yazısı konuşmanın çevirisini karıştırmaz. *Görünüm → Yerleşim → Oyun
  altyazısının üzerine* iki görünümden birinde açıkken devreye girer; ikisinde de kapalıyken hiçbir şey değişmez,
  okuma eskisi gibidir. Yakın duran yazılar da ayrılır: aralarındaki boşluk satır boyuna oranla ölçülür (aynı
  altyazının satırları bitişiktir, konuşma ile görev yazısı arasında bir satır kadar boşluk vardır).

### Değişti

- Önizleme düşük yükle çalışır: kare önizlemenin piksel genişliğine küçültülür, boşta 4, çeviri sürerken 2
  kare/sn; sayfa görünmüyorken ya da pencere simge durumundayken durur. Pencere modunda çeviri sürerken işlem
  hattının yakalaması paylaşılır (ikinci yakalama açılmaz). Ölçüm (1080p monitör): kare başına ~12,5 ms işlemci,
  boşta bir çekirdeğin ~%5'i, çeviri sürerken ~%2,5'i.
- Görünüm: "Oyun altyazısının üzerine" açıkken etkisiz ayarlar (Arka plan, İngilizce orijinal) gizlenir; Boyut'un
  altında üst sınır notu.
- Çeviri sayfasında Ollama modeli yalnız listeden seçilir (tıklayınca liste açılır, yazı yazılmaz). Listede
  önerilen modeller ve Ollama'da yüklü olanlar var; eski sürümde elle yazılmış model listeye eklenip seçili gelir.
- Pencere başlığında sürüm yazar ("Robz Vision – 1.3.x"). Kenar çubuğunda çeviri çalışırken nokta yeşil; sürüm
  satırında etiket ikonu. Ayarlar → Geliştirici'de YouTube bağlantısının yanında YouTube işareti.

### Düzeltildi

- Güncelleme penceresi, sürüm notları uzunsa notların sonuna kaydırılmış açılıyordu; artık baştan başlar.
- "Oyun altyazısının üzerine" yalnız bir görünümde açıkken (ör. yalnız *Yayınımda*) öbür görünümün altyazı
  kutusuna görev yazısı karışmaz; orada eskisi gibi yalnız konuşma görünür.
- Canlı önizleme Robz Vision kapanırken hata vermeden durur. Pencere modunda pencerenin görüntüsü değişmiyorsa
  (oyun duraklatıldı, pencere durgun) aynı kare yeniden küçültülüp çizilmez; işlemci boşuna çalışmaz.
- Satır içindeki kekeleme ("Just- just take it") birleştirilip çeviriye "Justjust" diye gidiyordu; tire artık yalnız
  satır sonundaysa (heceleme: "re-" + "treat") birleşir.
- Vurgulu kelimeler (oyunun pembe yazdığı isimler) bazen Türkçede vurgusuz kalıyordu:
  - Oyun yazıyı solarak getirdiğinde ilk okumada renk henüz yoktu ve aynı satır vurguyla yeniden çevrilmiyordu;
    artık sonradan beliren vurgu satırı yeniden çevirtir.
  - Yapay zekâ kısa cümlede vurgu işaretini atlarsa, çeviride aynen geçen isim ekiyle birlikte yine vurgulanır
    ("Choose what to do with Arnold" → "Arnold'a ne yapacağına karar ver", Arnold'a pembe).

## [1.3.2] - 2026-11-19

### Değişti

- Uygulama içinden güncellemenin ilk gerçek denemesi: 1.3.1'i kullananlar bu sürümü uygulamanın içinden
  (**Güncelle**) alabilir. Uygulamada başka değişiklik yok.

## [1.3.1] - 2026-11-19

### Eklendi

- **Geliştiriciyi Kick'te takip et:** kenar çubuğunun en altında Kick logolu küçük bir *Robz so good* bağlantısı
  ve *Ayarlar → Uygulama*'da *Geliştirici* satırı (Kick ve YouTube); tıklanınca kanal tarayıcıda açılır. Açılır
  pencere ya da bildirim yok, klavyeyle de açılır. Test düğmesinin örnek altyazısında da Kick daveti var; gerçek
  altyazı akışına ve OBS sayfasına hiçbir şey eklenmez. README, kullanım kılavuzu ve web sitesinde de bağlantı var.
- **README ve web sitesi yenilendi.** Örnek görseller artık GTA VI'nın resmî tanıtım görsellerinin üzerinde (oyun
  görseli © Rockstar Games / Take-Two Interactive): Türkçe kutu ve "Oyun altyazısının üzerine" kipi uygulamanın
  kendi çizim koduyla çizilir, altyazı cümleleri örnektir. README'nin başında büyük "Hemen indir" ve "Robz so
  good'u Kick'te takip et" düğmeleri, özellik tablosu, sayılarla hız ve sistem gereksinimleri. Görseller
  `tools/readme_gorselleri.py gta6 --kaynak KLASÖR` ile yeniden üretilir; kaynak görseller depoya girmez.
- **Uygulama içinden güncelleme:** Robz Vision yeni sürümü kendisi denetler (açılıştan kısa süre sonra, sonra
  saatte bir; GitHub'a kişisel bilgi içermeyen tek istek, *Ayarlar → Uygulama → Güncellemeler*'den kapatılabilir).
  Yeni sürüm varsa kenar çubuğunda **Güncelleme var** çıkar; çeviri çalışırken bildirim beklenir. **Güncelle**
  kurulum dosyasını indirir, boyutunu ve SHA256 özetini doğrular, Robz Vision'ı kapatıp sessizce kurar ve yeniden
  açar; ayarlar ve modeller korunur, ayar dosyası önce yedeklenir. **Sonra** ve **Bu sürümü atla** da var.
  Taşınabilir sürümde **İndir** sürüm sayfasını açar. 1.3.0 ve öncesi bir kez elle güncellenir.

## [1.3.0] - 2026-11-19

### Eklendi

- **Yalnız altyazıları oku** (*Ayarlar → Metin okuma*, varsayılan açık): araç plakası, tabela, neon, duvar yazısı,
  grafiti, köşedeki göstergeler ve tuş ipuçları gibi oyun içi yazılar çevrilmez; yalnız altyazı biçimindeki yazı
  (beyaz ya da açık gri, koyu kenarlı ya da gölgeli, ortalı) okunur. Robz Vision altyazının yerini ve boyunu
  okuduğu altyazılardan öğrenir; bölgeyi tam ortalı seçmeniz gerekmez. Altyazısı renkli, kenarsız ya da sola
  dayalı bir oyunda kapatın.
- **Oyun altyazısının üzerine** (*Görünüm → Yerleşim*, *Ekranımda* ve *Yayınımda* ayrı, varsayılan kapalı): Türkçe
  çeviri oyunun İngilizce altyazısının tam yerine yazılır. Koyu, tamamen kapalı bir kutu İngilizce yazıyı örter;
  kutu her satırda altyazının yerine ve genişliğine oturur, Türkçe içinde ortalanır ve oyunun yazısıyla aynı
  boydadır. Uzun çeviride yazı biraz küçülür, gerekirse kutu yukarı doğru büyür. Oyun altyazıyı kaldırınca kutu da
  kalkar. Windows ölçeklemesinde (%125, %150) ve birden çok ekranda doğru yere oturur.
- Katkıcılar için `tools/ocr_hiz.py` (OCR gecikmesi, doğruluğu ve işlemci yükü), `tools/altyazi_bicimi_testi.py`
  (altyazı süzgeci ve vurgu algılaması) ve `tools/hat_hizi.py` (altyazının belirmesinden Türkçesinin ekrana
  gelmesine kadar geçen süre). Üçü de kodla üretilen sahnelerle çalışır; telifli görsel kullanmaz.

### Değişti

- **OCR çok daha hızlı ve hafif.** Altyazının belirmesinden okunup çeviriye gitmesine kadar geçen süre ortalama
  ~400 ms'den ~100 ms'ye indi; okuma sırasında işlemci yükü yaklaşık 4 çekirdekten yarım çekirdeğe, yazı yokken
  ~1,7 çekirdekten ~0,3 çekirdeğe düştü. Yazı belirdiği karede hemen okunur; ilk okuma güçlüyse görüntü ikinci
  kez okunmaz; OCR motoru işlemcinin tamamını değil birkaç çekirdeğini kullanır; pencere yakalamada yeni kare
  gelmedikçe işlem yapılmaz. Varsayılan tarama hızı 20 kare/sn, kararlılık 1 kare (önceden 10 ve 2); bu iki ayarı
  değiştirmemiş kullanıcılar yeni değerlere geçer.
- Akıllı algılama artık hızlı konuşmada satır kaçırmıyor; *Sabit aralık* kipi de yazı değişince aralığı
  beklemeden okur.
- **Oyun profili ve "Ek bağlam" kaldırıldı.** Yapay zekâ her oyunda aynı, sade kurallarla çevirir: doğal Türkçe,
  kelimesi kelimesine değil; özel isimler (Vice City, Lucia, John) Türkçedeki kural gibi çevrilmez, ekleri
  kesme işaretiyle alır (Lucia'yı, Vice City'de). GTA 6 profili kaldırıldı; önceki satırlar (bağlam) yine
  modele gider. Eski profil ve notla yapılmış çeviriler önbellekten kullanılmaz.
- **Yapay zekâ çevirisi daha hızlı.** Önceki satırlar (bağlam) modele her satırda baştan işletilmiyor: bağlam
  yalnız sona eklenerek büyür, Ollama önceki isteğin işlenmiş kısmını yeniden kullanır. İstemin metni ve çeviri
  aynı kalır. Model, bir altyazı satırının çevirisinden çok daha uzun yazmaya kalkarsa erken kesilir.
- **Zayıf bilgisayarda boş ekranda bekleme yok.** Robz Vision yapay zekânın son satırlardaki hızını izler; çevirisi
  bekleme süresine yetişmeyecekse satır bekletilmez, önce hızlı çeviri görünür, yapay zekânınki gelince yerine
  geçer. Hibrit mod kapalıyken yapay zekâ satır başına 2 sn'den yavaşsa bir kez "Hibrit modu açın" bildirimi
  çıkar.
- **Hibrit mod kapalıyken de model yüklenirken altyazı akar.** Ayar artık yalnız yapay zekânın ne kadar
  bekleneceğini belirler: kapalıyken her satır yapay zekâyı bekler, ama model belleğe yüklenirken ya da yapay
  zekâ yanıt vermezse hızlı çeviri görünür.
- Durdur'a basınca model bellekten atılmaz: yeniden Başlat ve ayar değişikliği modeli baştan yüklemez (Gemma 4
  12B'de 5–100 sn). Model, Robz Vision kapanınca ya da başka bir modele veya motora geçilince boşaltılır. Uzun
  sessizlikte (oyun duraklatıldı) model bellekte sıcak tutulur.
- Yapay zekâya istek, hızlı çeviri hesaplanmadan gönderilir (Hibrit mod kapalıyken her satır ~25 ms erken).
- Ayar değişikliği ya da Durdur / Başlat sonrası çeviri hemen sürer: hızlı çeviri modeli bellekte kalır (her yeniden
  başlatmada ~0,8 sn diskten yükleniyordu, bu sırada altyazı çıkmıyordu).
- Yapay zekâ aynı kelimeyi tekrarlamaya girerse (Ollama isteği keser ya da çıktı sınırına takılır) satır tekrar
  cezasıyla bir kez daha istenir; önceden o satır hızlı çeviriye düşüyor ya da uzun bir yeniden üretim bekleniyordu.
- Ollama adresi `http://localhost` yazılmışsa `127.0.0.1` kullanılır: Windows'ta `localhost`a her yeni bağlantı
  önce IPv6'yı deneyip ~2 sn bekliyordu.

### Düzeltildi

- **Vurgu rengi vurgusuz kelimelerde çıkıyordu.** Yayın ve uzaktan oynatma görüntüsünde renkli zeminin (neon, gün
  batımı) rengi beyaz harflere taşıyor, kelime vurgulu sanılıyordu. Artık yalnız harflerin içi belirgin ve tek
  tonda renkliyse, renk zeminden gelmiyorsa vurgu çıkar ("Pay 'n' Spray" gibi araya giren kısa kelimeler de
  vurgunun parçası sayılır).
- Konuşmacı adındaki harf karışıklıkları ("LUClA", "JASoN") düzeltilir.
- "No.", "Go!" gibi iki harfli kısa yanıtlar hiç gösterilmiyordu.
- Yapay zekâ satır başına ~1,5 sn sürünce hızlı çeviri bir an görünüp hemen değişiyordu (göz kırpması). Bekleme
  artık OCR'da geçen süreyi ve yapay zekânın süresindeki oynamayı hesaba katar: ya yalnız yapay zekâ çevirisi
  görünür ya da hızlı çeviri baştan görünür ve yapay zekânınki gelince yerine geçer.
- Zayıf bilgisayarda yapay zekânın kuyruğu birikince çeviriler satır ekrandan kalktıktan saniyeler sonra
  geliyordu. Artık çevirisi geldiğinde ekranda olmayacak satır atlanır, yapay zekâ yeni satırlara kalır.
- **Başlat'a basınca "ısınıyor"da takılma.** Hibrit mod kapalıyken model belleğe yüklenene kadar (dakikayı
  bulabiliyordu) hiç altyazı çıkmıyordu; artık o sırada hızlı çeviri görünür ve durum satırı geçen süreyi
  gösterir ("Gemma 4 12B belleğe yükleniyor… 12 sn (şimdilik hızlı çeviri)").
- Model yüklenirken Durdur'a basılınca program yüklemenin bitmesini bekliyor, bu sırada basılan Başlat hiçbir şey
  yapmıyordu. Artık Durdur anında durur; kapanırken basılan Başlat kaybolmaz, kapanış bitince çalışır. Başlat'tan
  hemen sonra basılan Durdur da kaybolmaz: model boşuna yüklenmez.
- Çeviri sırasında Ollama kapanır ya da yeniden başlarsa yapay zekâ bir daha devreye girmiyordu ve Hibrit mod
  kapalıyken her satır ~2 sn geç görünüyordu. Artık satırlar beklemeden hızlı çeviriyle görünür; Robz Vision
  Ollama'yı yeniden başlatıp modeli yükler, hazır olunca yapay zekâ çevirisi geri gelir.
- Program kendiliğinden durduğunda (ör. OCR motoru açılamadı) model yüklemesi sürüyor, Başlat düğmesi "Durdur"
  olarak kalıyordu.

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

[Yayımlanmadı]: ../../compare/v1.4.1...HEAD
[1.4.1]: ../../releases/tag/v1.4.1
[1.4.0]: ../../releases/tag/v1.4.0
[1.3.2]: ../../releases/tag/v1.3.2
[1.3.1]: ../../releases/tag/v1.3.1
[1.3.0]: ../../releases/tag/v1.3.0
[1.2.1]: ../../releases/tag/v1.2.1
[1.2.0]: ../../releases/tag/v1.2.0
[1.1.0]: ../../releases/tag/v1.1.0
[1.0.4]: ../../releases/tag/v1.0.4
[1.0.3]: ../../releases/tag/v1.0.3
[1.0.2]: ../../releases/tag/v1.0.2
[1.0.1]: ../../releases/tag/v1.0.1
[1.0.0]: ../../releases/tag/v1.0.0
