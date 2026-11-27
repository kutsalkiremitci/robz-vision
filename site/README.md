# Robz Vision tanıtım sitesi

Tek sayfalık statik site: `index.html` (CSS ve JS içinde), `img/`, `fonts/` (Barlow, SIL OFL 1.1, lisans metni `fonts/OFL.txt`). Derleme adımı yoktur; klasör olduğu gibi yayımlanır ve doğrudan tarayıcıda da açılır.

- **GitHub Pages:** Depo ayarlarında *Pages → Build and deployment → Source* için *GitHub Actions* seçin. `.github/workflows/pages.yml` bu klasörü `main`'e her gönderimde yayımlar (özel depoda Pages ücretli plan ister).
- **Netlify:** *Add new site → Deploy manually* ile `site/` klasörünü sürükleyin ya da depoyu bağlayıp *Publish directory* olarak `site` yazın (build komutu boş).
- **Cloudflare Pages:** Depoyu bağlayın, *Build command* boş, *Build output directory* `site`.

Site şu adreste yayında: https://kutsalkiremitci.github.io/robz-vision/ . Başka bir adrese taşınırsa `index.html` içindeki `canonical`, `og:url`, `og:image` ve `twitter:image` adreslerini güncelleyin; sosyal ağlar göreli adresi okumaz.

Sayfa yalnızca GitHub API'sine istek atar (son sürüm ve dosya boyutu için). İstek başarısız olursa düğmeler sürüm sayfasına gider.

Dış bağlantılar (başka alan adına giden her `<a>`) yeni sekmede açılır: `target="_blank" rel="noopener noreferrer"`. Yeni bağlantı eklerken bunu yazın; betik de sayfa açılınca ve tıklanınca denetler. Sayfa içi çapalar ve sitedeki dosyalar hariçtir.

Uygulama ekran görüntüleri (`img/basla.webp`, `sihirbaz.webp`, `ana_pencere.webp`, `gorunum.webp`, `ceviri.webp`, `obs_yardim.webp`) `docs/gorseller/` altındaki PNG'lerden üretilir: WebP, `method=6`; `obs_yardim` kalite 88, diğerleri 82. Görsel değişince `<img>`'nin `width`/`height` değerlerini yeni boyuta göre güncelleyin.

## Kurulum sihirbazı

"Adım adım kurulum" bölümü (`#kurulum`) betik açıkken üç soru sorar (nerede oynuyorsunuz, ekran kartı, yayın), sonra kişiye özel planı adım adım gösterir: solda (telefonda üstte, açılır) adım listesi, sağda tek adım, altta yapışık *Geri* / *Yaptım, devam*. İlk adım "Neler lazım" (malzeme kartları, "Bende var"), son ekran "Hazırsınız". Betik kapalıyken bütün bölümler alt alta görünür. Plan ayrı bir metin değildir: `#kurulum`'daki sabit bölümler yanıtlara göre süzülüp ekranlara bölünür, yani metni yalnız bir yerde düzeltirsiniz.

- **Süzme:** `data-yol="2 3"`, `data-kart="12 8 yok x"`, `data-yayin="evet hayir x"` taşıyan öğe yalnız uyan yanıtta görünür (`x`: soru yanıtlanmadı). `data-wiz-skip` sihirbazda gizlenir; `class="wiz-only" hidden` yalnız sihirbazda görünür.
- **Ekranlar:** `data-adim="Başlık"` taşıyan adım (`li`) yeni bir ekran başlatır; sonraki adımlar aynı ekranda kalır. Listenin dışındaki notlar ve açılır kutular önceki adımın ekranına gider; `data-adim-ek="Başlık"` başka bir ekrana bağlar. Yeni adım eklerken ya mevcut bir ekrana koyun ya da `data-adim` ile yeni ekran açın.
- **Malzeme kartları:** "Neler lazım" listesindeki her öğe `data-need="anahtar"` taşır; kartın çizimi mevcut görsellerden kırpılır (kırpma ölçüleri betikte, `ART`).
- **Adres:** `#kurulum` (1. soru), `#kurulum?soru=2&yol=3` (soru ekranı), `#kurulum?yol=3&kart=12&yayin=evet&adim=4` (planın 4. adımı; `adim` yoksa 1). Her adım geçmişe yazılır, tarayıcının geri düğmesi önceki adıma döner. Eski `#yol-1` … `#yol-4` bağlantıları o yolun planını açar.
- **Klavye:** Plandayken ← → adım değiştirir (odak bir giriş alanında değilken).
- **İşaretler:** Adımlardaki ve malzeme kartlarındaki işaretler yalnız ziyaretçinin tarayıcısında (`localStorage`, `robzvision-kurulum`) tutulur. Kimlik adım metninden türer; metin değişirse o adımın işareti düşer.
- **Hareket:** Geçişler yalnız süstür (kayma, saydamlık değişmez); canlandırma oynamasa da içerik görünür. `prefers-reduced-motion` açıkken hareket yoktur.
