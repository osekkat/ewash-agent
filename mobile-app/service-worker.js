/* ewash PWA service worker
 * Strategy: network-first. Serve fresh from network when online; fall back
 * to cache only if the network fails (e.g. offline). The cache is kept
 * warm in the background so the PWA still works fully offline.
 *
 * Tradeoff: every request pays a network round-trip when online.
 * Win: no version-bump dance — code edits show up on the next reload.
 *
 * Versioning:
 *   Bump SW_VERSION on every SW behavior change. The CACHE_NAME embeds the
 *   version so the `activate` handler can evict every prior cache in a single
 *   `caches.keys()` pass. Vercel serves this file with `Cache-Control:
 *   no-store` (see mobile-app/vercel.json), so browsers always revalidate it
 *   and the new version takes over on the next page load.
 */
const SW_VERSION = '2026-05-19-hero-drop-eco-chip';
const CACHE = `ewash-${SW_VERSION}`;
const SW_LOG_LIMIT = 100;
let swLogCount = 0;
const ASSETS = [
  './',
  './index.html',
  './styles.css',
  './i18n.js',
  './icons.jsx',
  './tweaks-panel.jsx',
  './components.jsx',
  './auth.jsx',
  './screens.jsx',
  './booking.jsx',
  './app.jsx',
  './manifest.webmanifest',
  './assets/ewash-logo.png',
  './assets/icon-192.png',
  './assets/icon-512.png',
  './assets/icon-maskable-512.png',
  './assets/apple-touch-icon.png',
];

self.EwashLog = {
  info(scope, payload) {
    if (swLogCount >= SW_LOG_LIMIT) return;
    swLogCount += 1;
    console.info(`[ewash.${scope}]`, {
      t: new Date().toISOString(),
      level: 'info',
      scope: `ewash.${scope}`,
      version: SW_VERSION,
      cache_name: CACHE,
      ...(payload || {}),
    });
  },
  warn(scope, payload) {
    if (swLogCount >= SW_LOG_LIMIT) return;
    swLogCount += 1;
    console.warn(`[ewash.${scope}.warn]`, {
      t: new Date().toISOString(),
      level: 'warn',
      scope: `ewash.${scope}`,
      version: SW_VERSION,
      cache_name: CACHE,
      ...(payload || {}),
    });
  },
};

self.addEventListener('install', (event) => {
  self.EwashLog.info('sw.install', { asset_count: ASSETS.length });
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(
        ASSETS.map((url) => new Request(url, { cache: 'reload' }))
      ))
      .then(() => self.skipWaiting())
      .catch((err) => {
        self.EwashLog.warn('sw.install', {
          error_code: (err && err.message) || 'install_failed',
        });
      })
  );
});

self.addEventListener('activate', (event) => {
  self.EwashLog.info('sw.activate', {});
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
      .catch((err) => {
        self.EwashLog.warn('sw.activate', {
          error_code: (err && err.message) || 'activate_failed',
        });
      })
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // CRITICAL: never let the SW touch /api/* requests. The PWA's bookings list,
  // catalog, and auth state must reflect server state on every read — caching
  // any of them produces stale-data bugs (latest booking missing, admin price
  // edits invisible, revoked token returning a cached 200). Applies to every
  // method: GETs to avoid stale reads, POSTs so the cross-origin booking
  // submission isn't accidentally cached, OPTIONS so preflight responses
  // pull live CORS headers when ALLOWED_ORIGINS rotates.
  let url;
  try {
    url = new URL(req.url);
  } catch (err) {
    url = null;
  }
  if (url && url.pathname.startsWith('/api/')) {
    self.EwashLog.info('sw.fetch', {
      path: url.pathname,
      method: req.method,
      bypassed: true,
    });
    return;
  }

  if (req.method !== 'GET') return;

  event.respondWith(
    fetch(req).then((response) => {
      if (response && response.status === 200 &&
          (response.type === 'basic' || response.type === 'cors')) {
        const clone = response.clone();
        caches.open(CACHE)
          .then((c) => c.put(req, clone))
          .catch((err) => {
            self.EwashLog.warn('sw.fetch', {
              path: url ? url.pathname : '',
              method: req.method,
              error_code: (err && err.message) || 'cache_put_failed',
            });
          });
      }
      return response;
    }).catch(() => caches.match(req))
  );
});
