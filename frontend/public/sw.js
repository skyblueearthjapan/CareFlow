// CareFlow minimal Service Worker (Wave 2-E revise)
// - install: precache /offline.html, skipWaiting
// - activate: clients.claim, drop stale cache versions
// - fetch:
//   * /api/*           -> network-only (PII / session / auth never cached)
//   * /login, /logout* -> network-only (avoid stale auth nav HTML)
//   * /_next/static/*  -> cache-first
//   * navigation       -> network-first; offline fallback /offline.html
//   * other GET        -> network-first with LRU runtime cache (max 50)
//
// NOTE: bump CACHE_VERSION on every release until BUILD_ID is wired in
// (Phase 9 TODO: derive from next.config.js generateBuildId / GIT_SHA).
const CACHE_VERSION = 'careflow-v2';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;
const RUNTIME_MAX_ENTRIES = 50;
const OFFLINE_URL = '/offline.html';

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

async function trimCache(cacheName, maxEntries) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length <= maxEntries) return;
  // FIFO eviction (sufficient for residual bound; full LRU = Phase 9).
  const overflow = keys.length - maxEntries;
  for (let i = 0; i < overflow; i += 1) {
    await cache.delete(keys[i]);
  }
}

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

  // Next static assets: cache-first.
  if (url.pathname.startsWith('/_next/static/')) {
    event.respondWith(
      (async () => {
        const cache = await caches.open(STATIC_CACHE);
        const cached = await cache.match(req);
        if (cached) return cached;
        try {
          const res = await fetch(req);
          if (res.ok) cache.put(req, res.clone());
          return res;
        } catch (err) {
          if (cached) return cached;
          throw err;
        }
      })()
    );
    return;
  }

  const isNavigation =
    req.mode === 'navigate' ||
    (req.headers.get('accept') || '').includes('text/html');

  // Default: network-first; on failure fall back to cache, then offline page for nav.
  event.respondWith(
    (async () => {
      const cache = await caches.open(RUNTIME_CACHE);
      try {
        const res = await fetch(req);
        if (res.ok) {
          cache.put(req, res.clone());
          // Async LRU trim — don't block the response.
          trimCache(RUNTIME_CACHE, RUNTIME_MAX_ENTRIES).catch(() => {});
        }
        return res;
      } catch (err) {
        const cached = await cache.match(req);
        if (cached) return cached;
        if (isNavigation) {
          const offline = await caches.match(OFFLINE_URL);
          if (offline) return offline;
        }
        throw err;
      }
    })()
  );
});
