# Robz Vision tasarım sistemi — "Ocean Drive gecesi"

**Konu:** Canlı yayın yapan bir oyuncunun, oyun oynarken göz ucuyla baktığı altyazı çevirmeni.
**Kitle:** Kick/Twitch yayıncıları; yayın sırasında hızlı karar veren, ayarla uğraşmak istemeyen kişiler.
**Birincil iş:** Bölge seç → Başlat → çeviriyi gör. Gerisi (görünüm, motor, OCR) ikincil.

**Görsel yön:** Vice City / GTA VI esintisi, ama kopya değil. Logodaki mor-lacivert gecede sakin yüzeyler,
logonun neon tabelalarından ödünç alınan iki vurgu (gün batımı pembesi, neon camgöbeği) ve Florida yol
tabelalarını çağrıştıran DIN çizgisinde başlıklar. Pahalı ve ölçülü görünmeli; hayran sitesi gibi değil.

Ürün adı arayüz kodunda elle yazılmaz: `from altyazitr import APP_NAME`.

## Marka (gui/theme.py)

- Marka işareti hazır raster logodur: `altyazitr/data/logo.png` (512×512, yuvarlak köşeli kare,
  neon "RV", palmiyeler ve şehir silueti). Kodda **marka işareti çizilmez**.
- `theme.make_icon_pixmap(size)` logoyu yumuşak ölçekleyip önbellekten verir; dosya yoksa sade bir
  yer tutucu döner. `theme.app_icon()` 16/20/24/32/48/64/128/256 boyutlarından QIcon kurar.
- Logo kendi renkleriyle durur; üstüne çerçeve, gölge veya geçiş eklenmez.

## Renk (gui/tokens.py → `C`)

Yüzeyler logonun mor-lacivert gecesine göre hafif mor tonludur; vurgular logodaki neon renklerdir.

| Token | HEX | Görev |
|---|---|---|
| `BG` gece yarısı | `#0C0B1D` | uygulama zemini |
| `PANEL` lacivert asfalt | `#13122A` | kenar çubuğu, canlı başlık bandı, tablo, salt okunur alan |
| `RAISED` okyanus gecesi | `#1C1A38` | giriş alanları, ikincil düğmeler, açılır menüler |
| `HOVER` / `PRESSED` | `#262448` / `#17152F` | üzerine gelme / basılı |
| `LINE` / `LINE_STRONG` | `#23203F` / `#3B3762` | bölüm ayırıcı / giriş ve düğme kenarı |
| `PINK` gün batımı | `#FF4D94` | **birincil eylem** (Başlat, Kaydet), ilerleme çubuğu |
| `CYAN` neon | `#3FD8EE` | klavye odağı, seçili öğe, bağlantılar, kaydırıcı dolgusu |
| `TURQUOISE` tropik | `#2BC4AE` | **canlı/çalışıyor**, başarı |
| `ORANGE` mercan | `#FF8A5B` | uyarı |
| `DANGER` | `#FF5A5F` | durdur, hata |
| `TEXT / TEXT_2 / TEXT_3` | `#F5F3FB / #AEAAC6 / #8A86A6` | birincil / ikincil / yer tutucu-ipucu yazı |
| `TEXT_DISABLED` | `#5C5878` | devre dışı denetimler |
| `INVERSE` | `#0C0B1D` | pembe/camgöbeği/turkuaz dolgu üstündeki yazı |
| `*_SOFT` | yarı saydam | seçili/durum zeminleri (yalnızca QSS; QPainter'da `tokens.qcolor()`) |

Kontrast (WCAG): `TEXT` ≥ 13:1, `TEXT_2` ≥ 6.5:1, `TEXT_3` ≥ 4.5:1 (`RAISED` üstünde dahil);
`INVERSE` pembe üstünde 6.2:1, camgöbeği üstünde 11:1.

Kurallar:
- Renk **anlam** taşır, süs değildir. Bir ekranda en fazla bir pembe birincil düğme olur.
- Yüzeyler arasındaki ayrım ton farkı ve 1 px `LINE` çizgisiyle yapılır; gölge yok.
- **Gün batımı geçişi** (pembe → turuncu, `SUNSET_STOPS`) yalnızca canlı başlık bandının alt
  çizgisinde çizilir. Başka hiçbir yerde geçiş yok.
- Neon parlaması (glow), bulanıklık, doygun dolgu alanları yok.
- QSS'te yarı saydam renk gerekiyorsa `tokens.rgba(C.X, alfa)` kullanılır; HEX yazılmaz.

## Yazı (gui/tokens.py → `F`)

- **Bahnschrift** (Windows'ta hazır; DIN, yol tabelası karakteri): sayfa başlıkları
  (`Bahnschrift SemiBold SemiCondensed`, 26 px), bölüm başlıkları (`Bahnschrift SemiBold`, 16 px),
  düğmeler (14 px; birincil/durdur SemiBold), segmentler, tablo başlıkları, durum yazısı ve sayılar
  (ms, MB, px değerleri, sayı kutuları, kısayol tuşları).
- **Segoe UI Variable Text** 10 pt (yoksa Segoe UI): uygulama yazı tipi; gövde, form etiketleri, ipuçları.
  Son çevirilerde Türkçe 14 px gövde yazısı, konuşmacı adı `Segoe UI Variable Display Semibold`, İngilizce
  12 px `TEXT_2`, saat Bahnschrift 12 px `TEXT_3`.
- Uygulama yazı tipi `app.setFont()` ile verilir, QSS'te genel `*` yazı kuralı yoktur; bu yüzden
  bir öğede `setFont()` çalışır (örn. yazı tipi seçici seçili aileyi kendi yüzüyle gösterir).
- Cümle düzeni. **BÜYÜK HARF etiket yok**, başlık üstü küçük "eyebrow" etiketi yok.
- "A · B · C" gibi orta noktalı meta dizileri yok; ayrı öğeler olarak yerleştirin.
- Monospace etiket yok; sayılar Bahnschrift ile yazılır.
- Satır uzunluğu 80 karakterin altında; açıklamalar ipucu (tooltip) olarak.
- **"İ" noktası:** Bahnschrift'te büyük İ'nin noktası satır üstünü aşar. Tema, Bahnschrift etiketlerine
  2–3 px üst boşluk verir. Seçilebilir (`TextSelectableByMouse`) QLabel bu noktayı her durumda kırpar;
  bu yüzden Bahnschrift stilli etiketler seçilebilir yapılmaz (`widgets.label` bunu `DISPLAY_LABELS`
  için kendisi atlar). Kendi `setFont`'uyla Bahnschrift kullanan etiketlere de üstten pay bırakın.

## Düzen

```
┌──────────────┬───────────────────────────────────────────────────────┐
│ [logo] Robz  │ Ana sayfa                                             │
│        Vision│ ┌─ canlı başlık bandı (PANEL) ──────────────────────┐ │
│ ikon Ana s.  │ │ [Başlat]   Hazır                  [Test] [Gizle]  │ │
│ ikon Görünüm │ │ durum satırı                                      │ │
│ ikon Çeviri  │ └━━━━━━━━━━━━━━ gün batımı çizgisi ━━━━━━━━━━━━━━━━━┘ │
│ ikon Ayarlar │                                                       │
│              │ ───────────────────────────────────────────────────── │
│              │ Çevrilecek bölge                  [Ekran | Pencere]   │
│              │ [ canlı önizleme ............................ ]       │
│ nokta Durum  │ [Bölge seç]                           Metin var       │
│ nokta OBS    │ ───────────────────────────────────────────────────── │
└──────────────┴ Son çeviriler …                                       ┘
```

- İçerik **sola hizalı**; bölümler kart ızgarası değil, üstte 1 px ayırıcı + başlık + boşlukla ayrılan
  bantlardır. Yalnızca canlı başlık bandı ve önizleme bir yüzeye oturur.
- `widgets.page(title)`: sayfa kenar boşluğu 32 px, bölümler arası 24 px, başlık `#pageTitle`.
- **Sayfa düzeyinde seçim** (Görünüm'deki *Ekranımda | Yayınımda*): sayfanın tüm denetimlerinin neyi
  düzenlediğini belirleyen seçim, başlığın altında **kaydırılmayan** üst bantta durur (`page(None)` + kendi
  bandı; başlığın yeri diğer sayfalarla aynı). Yanında yalnız ona bağlı ikincil eylem (`#ghost`), altında tek
  satır `#muted` açıklama; bant ile içerik arası yine 24 px. Önizleme, neyi gösterdiğini köşe etiketinde söyler.
  `widgets.Card(title)`: sade bölüm (`QFrame#card`), ayırıcıdan sonra 20 px, öğeler arası 12 px.
- Boşluk ölçeği: 4 / 8 / 12 / 16 / 24 / 32 px (`SPACE`).
- Köşe: girişler ve düğmeler 6 px, segment içi 5 px, açılır yüzeyler/bant/önizleme/hazır stil kartı
  10 px (`RADIUS`). Her şey aynı yuvarlaklıkta değil.
- Kenar çubuğu: ikon + metin (dar pencerede yalnız ikon); seçili sayfa pembe değil, 3 px **camgöbeği**
  sol çizgi + `RAISED` zemin. Alt kısımda canlı durum ve OBS bağlantı durumu. Kenar çubuğu kendi yerel
  stil sayfasıyla çizilir.
- En küçük pencere 780×560; metin taşmaz.

## Bileşen sözleşmesi (QSS seçicileri — gui/theme.py)

| Seçici | Görünüm |
|---|---|
| `QPushButton` (varsayılan) | ikincil: `RAISED` zemin, `LINE_STRONG` kenar, Bahnschrift 14 px, yükseklik ~32 px |
| `QPushButton#primary` | pembe dolgu, `INVERSE` yazı, SemiBold; hover `PINK_HOVER`, basılı `PINK_PRESSED` |
| `QPushButton#danger` | durdur: `DANGER` kenar + yazı, saydam zemin; hover `DANGER_SOFT` |
| `QPushButton#ghost` | saydam, yalnız ikon/metin `TEXT_2`; hover `HOVER` |
| `QPushButton#icon` | 32×32 kare ikon düğmesi (ikincil görünüm); her zaman tooltip + accessibleName |
| `QPushButton:checked` | `CYAN_SOFT` zemin + `CYAN` kenar |
| `QFrame#segmented` › `segL / segM / segR` | segmentli seçici: `RAISED` kanal; seçili segment `CYAN_SOFT` zemin + `CYAN` kenar |
| `QPushButton#collapse` | katlanır bölüm başlığı: chevron ikonu + cümle düzeninde metin |
| `QPushButton#preset` | hazır stil kartı (`PANEL`, 10 px); seçiliyken camgöbeği halka + onay rozeti kodda çizilir |
| `QPushButton#swatch` | renk yuvarlağı (kodda çizilir): seçili = camgöbeği halka + onay ikonu |
| `QLabel#pageTitle` | sayfa başlığı, Bahnschrift SemiBold SemiCondensed 26 px |
| `QLabel#section` | bölüm başlığı, Bahnschrift SemiBold 16 px |
| `QLabel#status` | canlı banttaki durum yazısı, Bahnschrift SemiBold 22 px |
| `QLabel#value` | sayısal değer (44 px, %80, 1.0x), Bahnschrift 14 px `TEXT_2` |
| `QLabel#muted`, `#field` | ikincil yazı / form etiketi (`#field` en az 120 px) |
| `QLabel#pill`, `#pillOn`, `#pillWarn` | 22 px durum rozeti: pasif / canlı (turkuaz) / uyarı (mercan) |
| `QListView#history` | son çeviriler (`gui/history.py`): `PANEL` yüzey, satırları kendi delegesi çizer: saat sütunu + konuşmacı/Türkçe + küçük İngilizce, aralarında 1 px `LINE`. Üzerine gelince `RAISED` zemin ve sağda kopyala ikonu; odaktayken seçili satır `CYAN_SOFT`. En yenisi üstte; yükseklik içeriğe göre büyür, 300 px'te durur |
| `QLabel#preview` | bölge önizlemesi: `PANEL` + 1 px `LINE`, 10 px |
| `QFrame#card` | **sade bölüm**: zemin yok, üstte 1 px `LINE` ayırıcı (geriye uyumluluk için adı korunur) |
| `QFrame#live` | canlı başlık bandı: `PANEL` + 1 px `LINE`, 10 px; alt 2 px çizgiyi `LiveBand` çizer |
| `QLineEdit`, `QComboBox`, `QSpinBox` | `RAISED` zemin, `LINE_STRONG` kenar, 6 px; odakta `CYAN` kenar; salt okunur `PANEL` |
| `QLineEdit#hotkey` | kısayol kutusu: Bahnschrift; odaktayken (tuş dinlerken) `CYAN_SOFT` zemin |
| `QComboBox` açılır listesi | `RAISED`, öğe 28 px; hover `HOVER`, seçili `CYAN_SOFT` (`combobox-popup: 0`) |
| `QCheckBox` | 18 px; işaretli = `CYAN` dolgu + koyu onay |
| `QSlider` | 4 px oluk `LINE_STRONG`, dolgu `CYAN`, beyaz tutamak (hover camgöbeği) |
| `QProgressBar` | 6 px ince çubuk, `PINK` dolgu; yüzde yazısı gösterilmez |
| `QTableWidget` / `QHeaderView` | `PANEL`, ızgara `LINE`; başlık Bahnschrift `TEXT_2`; seçili satır `CYAN_SOFT` |
| `QMenu`, `QToolTip`, `QMessageBox` | `RAISED` yüzey + `LINE_STRONG` kenar; menü öğesi hover `HOVER` |
| `QScrollBar` | 8 px tutamak `LINE_STRONG`, hover `TEXT_3`; ok düğmesi yok |
| devre dışı | yazı `TEXT_DISABLED`, kenar `LINE`, dolgu kalkar |

### Odak ("focus-visible")

`theme._FocusRing`, `QApplication.focusChanged` sinyaliyle odaklanan öğeye `kbfocus` özelliği verir:
odak Tab/kısayolla geldiyse `true`. QSS düğme, segment, onay kutusu, kaydırıcı, renk yuvarlağı ve
katlanır başlıkta camgöbeği halkayı `[kbfocus="true"]:focus` ile çizer; fareyle tıklamada halka çıkmaz.
Metin giriş alanlarında odak her zaman görünür (`:focus`). Olay süzgeci yoktur.

### Yardımcılar (gui/widgets.py)

- `button_icon(name, color, size=16, gap=4, on_color=None) → (QIcon, QSize)`: Qt ikonla metin arasına
  yalnız 4 px koyar; bu ikon sağında saydam pay taşır, aralık ~8 px olur. İkonlu düğmelerde kullanın.
- `color_chip_qss(color, ring=None)`: kullanıcının seçtiği rengi zemin yapan düğme stili.
- `bind_spin` sayı kutusunun metnine soldan pay verir (QSS yatay iç boşluğu orada çalışmaz).

## İkonlar (gui/icons.py)

Uygulamanın kendi çizgi ikon seti: 24 birim ızgara, 1.75 çizgi, yuvarlak uç/birleşim, dolgusuz.
`icon(name, color)` ile kullanılır; ikon tek başına anlamı taşımıyorsa tooltip eklenir.
Arayüzde **emoji veya Unicode simge (▶ ■ ✔ ⚠ ↻ ⓘ) kullanılmaz**.

## Hareket

- Yalnızca kullanıcı eylemine yanıt: düğme basma, segment değişimi, katlanır bölüm açılması,
  altyazı belirme/kaybolma, canlı bant çizgisinin turkuaza dönmesi. Kendiliğinden oynayan animasyon yok.
- Süreler: 120 ms (hover/basma), 180 ms (açılma, belirme). `tokens.motion_ms()` Windows'un
  "Animasyonları göster" ayarı kapalıysa 0 döndürür.

## Yazım

Kullanıcının dilinde, etken çatı, kısa: "Bölge seç", "Kaydet", "Konumu ayarla". Bir eylem akış boyunca
aynı adı taşır. Hata mesajı ne olduğunu ve nasıl düzeleceğini söyler; özür dilemez. Boş durum bir eyleme
davettir ("Henüz bölge seçilmedi" + **Bölge seç** düğmesi).

## Cesaret tek yerde

Akılda kalan öğe **canlı başlık bandıdır**: büyük Bahnschrift durum yazısı ve altındaki gün batımı
çizgisi. Çalışırken çizgi turkuaza döner. Geri kalan her şey sessiz ve disiplinli.
