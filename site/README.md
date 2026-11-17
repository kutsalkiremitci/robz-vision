# Robz Vision tanıtım sitesi

Tek sayfalık statik site: `index.html` (CSS ve JS içinde), `img/`, `fonts/` (Barlow, SIL OFL 1.1, lisans metni `fonts/OFL.txt`). Derleme adımı yoktur; klasör olduğu gibi yayımlanır ve doğrudan tarayıcıda da açılır.

- **GitHub Pages:** Depo ayarlarında *Pages → Build and deployment → Source* için *GitHub Actions* seçin. `.github/workflows/pages.yml` bu klasörü `main`'e her gönderimde yayımlar (özel depoda Pages ücretli plan ister).
- **Netlify:** *Add new site → Deploy manually* ile `site/` klasörünü sürükleyin ya da depoyu bağlayıp *Publish directory* olarak `site` yazın (build komutu boş).
- **Cloudflare Pages:** Depoyu bağlayın, *Build command* boş, *Build output directory* `site`.

Site şu adreste yayında: https://kutsalkiremitci.github.io/robz-vision/ . Başka bir adrese taşınırsa `index.html` içindeki `canonical`, `og:url`, `og:image` ve `twitter:image` adreslerini güncelleyin; sosyal ağlar göreli adresi okumaz.

Sayfa yalnızca GitHub API'sine istek atar (son sürüm ve dosya boyutu için). İstek başarısız olursa düğmeler sürüm sayfasına gider.
