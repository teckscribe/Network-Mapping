const CACHE_NAME = 'gpon-survey-v2';
const ASSETS_TO_CACHE = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './preload_data.js',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './vendor/leaflet.js',
  './vendor/leaflet.css',
  './vendor/xlsx.full.min.js',
  './vendor/marker-icon.png',
  './vendor/marker-shadow.png',
  './vendor/marker-icon-2x.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pre-caching offline assets');
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('[SW] Clearing old cache', key);
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Stale-while-revalidate strategy for local assets, fallback to cache
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request).catch(() => {
        // Return offline fallback if network fails
        if (event.request.mode === 'navigate') {
          return caches.match('./index.html');
        }
      });
    })
  );
});
