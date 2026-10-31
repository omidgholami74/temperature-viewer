/**
 * Furnace dashboard service worker.
 *
 * The shell is cached so an installed app opens instantly and still shows the
 * last screen offline. API calls are never cached: a stale temperature is worse
 * than an honest "offline" banner.
 *
 * Bump CACHE_VERSION whenever the shell changes; the old cache is removed on
 * activate.
 */
const CACHE_VERSION = 'furnace-v10';

const SHELL = [
  './',
  './index.html',
  './config.js',
  './manifest.webmanifest',
  './assets/css/tokens.css',
  './assets/css/app.css',
  './assets/js/theme-boot.js',
  './assets/js/main.js',
  './assets/js/api.js',
  './assets/js/feed.js',
  './assets/js/chart-panel.js',
  './assets/js/format.js',
  './assets/js/drop-filter.js',
  './assets/vendor/chart.umd.min.js',
  './assets/img/favicon.svg',
  './assets/img/logo-razi.svg',
  './assets/img/icon-180.png',
  './assets/img/icon-192.png',
  './assets/img/icon-512.png',
  './assets/img/icon-maskable-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(async (cache) => {
      // Add one by one: a single missing file must not block the whole install.
      await Promise.all(
        SHELL.map((url) => cache.add(new Request(url, { cache: 'reload' })).catch(() => null)),
      );
      await self.skipWaiting();
    }),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(async (keys) => {
      await Promise.all(
        keys.filter((key) => key !== CACHE_VERSION).map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    }),
  );
});

function isApiRequest(url) {
  return url.pathname === '/api' || url.pathname.startsWith('/api/') || url.pathname.includes('/api/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;

  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);
  const sameOrigin = url.origin === self.location.origin;

  // Live data, health checks and CSV exports always go to the network.
  if (!sameOrigin || isApiRequest(url)) {
    return;
  }

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put('./index.html', copy)).catch(() => {});
          return response;
        })
        .catch(async () => (await caches.match(request, { ignoreSearch: true }))
          ?? (await caches.match('./index.html', { ignoreSearch: true }))
          ?? new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } })),
    );
    return;
  }

  // Static assets: serve the cached copy immediately and refresh it in the
  // background, so a deploy is picked up on the next launch.
  event.respondWith(
    caches.match(request, { ignoreSearch: true }).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => cached);

      return cached ?? network;
    }),
  );
});
