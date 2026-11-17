// OBS, tarayıcı kaynağını Robz Vision'dan önce açarsa sayfa yüklenemez ve OBS yeniden denemez;
// kaynak, elle yenilenene kadar boş kalır. Bu service worker sayfanın son kopyasını saklar:
// sunucu kapalıyken o kopya açılır, sayfa da uygulama açılana kadar bağlanmayı dener.
const CACHE = 'robz-vision-1';

self.addEventListener('install', (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.add('/')));
});

self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (e) => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put('/', copy));
        }
        return res;
      })
      .catch(() => caches.match('/').then((r) => r || Response.error()))
  );
});
