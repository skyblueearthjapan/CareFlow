// CareFlow minimal Service Worker (Wave 2-E revise2)
// - install: precache /offline.html, skipWaiting
// - activate: clients.claim, drop stale cache versions
// - fetch:
//   * /api/*                            -> network-only (PII / session / auth never cached)
//   * /login, /logout*                  -> network-only (avoid stale auth nav HTML)
//   * cacheable allowlist (static/icon/manifest/offline)
//                                       -> cache-first
//   * everything else (HTML / RSC / _next/data / cookie-scoped pages)
//                                       -> network-only; navigation may fall back to /offline.html
//
// CACHE_VERSION is replaced at Docker build time via `sed -i` in frontend/Dockerfile
// (`__BUILD_ID__` -> $GIT_SHA). Falls back to 'dev' for local builds.
const CACHE_VERSION = 'careflow-__BUILD_ID__';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const OFFLINE_URL = '/offline.html';

// Explicit allowlist for cache-first behavior. HTML/RSC/cookie-scoped responses
// are intentionally excluded to prevent PII pollution in Cache Storage.
function isCacheable(url) {
  if (url.pathname.startsWith('/_next/static/')) return true;
  if (url.pathname.startsWith('/icons/')) return true;
  if (url.pathname.startsWith('/fonts/')) return true;
  if (url.pathname === '/manifest.webmanifest' || url.pathname === '/manifest.json') return true;
  if (url.pathname === '/offline.html') return true;
  if (url.pathname === '/favicon.ico') return true;
  return false;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(STATIC_CACHE);
      await cache.addAll([OFFLINE_URL]);
      await self.skipWaiting();
    })()
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      );
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Same-origin only
  if (url.origin !== self.location.origin) return;

  // /api/* (auth, v1, etc): network-only — never cache PII or session data.
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(req));
    return;
  }

  // Login / logout navigation: network-only to avoid stale auth HTML.
  if (url.pathname === '/login' || url.pathname.includes('/logout')) {
    event.respondWith(fetch(req));
    return;
  }

  const isNavigation =
    req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');

  // Allowlisted public assets: cache-first.
  if (isCacheable(url)) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(STATIC_CACHE);
        const cached = await cache.match(req);
        if (cached) return cached;
        try {
          const res = await fetch(req);
          if (res.ok) {
            // Await put before returning so the cache write is durable; no
            // trim needed because the allowlist is bounded by static surface.
            await cache.put(req, res.clone());
          }
          return res;
        } catch (err) {
          if (cached) return cached;
          throw err;
        }
      })()
    );
    return;
  }

  // Everything else (authenticated HTML, RSC payloads, /_next/data, cookie-
  // scoped pages, etc.): network-only. Navigation requests fall back to the
  // precached offline shell when the network is unavailable.
  event.respondWith(
    (async () => {
      try {
        return await fetch(req);
      } catch (err) {
        if (isNavigation) {
          const offline = await caches.match(OFFLINE_URL);
          if (offline) return offline;
        }
        throw err;
      }
    })()
  );
});
