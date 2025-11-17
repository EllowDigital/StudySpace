// Service Worker for TypeBlitz PWA

const VERSION_ENDPOINT = "/production/latest/version.txt";
const DEFAULT_CACHE = "typeblitz-cache-stable";
const PRECACHE_URLS = [
  "/",
  "/index.html",
  "/assets/images/TypeBlitz.png",
  "/assets/TypeBlitz-gif.gif",
  "/assets/favicon/favicon.ico",
  "/assets/favicon/apple-touch-icon.png",
  "/assets/favicon/web-app-manifest-192x192.png",
  "/assets/favicon/web-app-manifest-512x512.png",
  VERSION_ENDPOINT,
];

const cacheNamePromise = fetch(VERSION_ENDPOINT, { cache: "no-store" })
  .then((response) => (response.ok ? response.text() : null))
  .then((text) => {
    const version = text?.trim();
    return version ? `typeblitz-cache-${version}` : DEFAULT_CACHE;
  })
  .catch(() => DEFAULT_CACHE);

const resolveCacheName = () => cacheNamePromise;

const withCache = async () => {
  const cacheName = await resolveCacheName();
  return caches.open(cacheName);
};

// Install a service worker
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await withCache();
      await cache.addAll(PRECACHE_URLS);
      self.skipWaiting();
    })()
  );
});

// Cache and return requests
self.addEventListener("fetch", (event) => {
  const { request } = event;

  if (request.method !== "GET") {
    return;
  }

  event.respondWith(
    (async () => {
      const cache = await withCache();

      const isNavigation = request.mode === "navigate";
      const shouldBypassCache = request.url.includes("version.txt");

      if (shouldBypassCache) {
        return fetch(request, { cache: "no-store" });
      }

      if (isNavigation) {
        try {
          const networkResponse = await fetch(request, { cache: "no-store" });
          cache.put(request, networkResponse.clone());
          return networkResponse;
        } catch (error) {
          const cachedResponse = await cache.match(request);
          if (cachedResponse) {
            return cachedResponse;
          }
          return cache.match("/index.html");
        }
      }

      const cachedResponse = await cache.match(request);
      if (cachedResponse) {
        fetch(request)
          .then((networkResponse) => {
            if (
              networkResponse &&
              networkResponse.status === 200 &&
              networkResponse.type === "basic"
            ) {
              cache.put(request, networkResponse.clone());
            }
          })
          .catch(() => {
            /* ignore */
          });
        return cachedResponse;
      }

      const networkResponse = await fetch(request);
      if (networkResponse && networkResponse.status === 200 && networkResponse.type === "basic") {
        cache.put(request, networkResponse.clone());
      }
      return networkResponse;
    })()
  );
});

// Update a service worker
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const activeCacheName = await resolveCacheName();
      const keys = await caches.keys();
      await Promise.all(
        keys.map((cacheName) => {
          if (cacheName !== activeCacheName && cacheName.startsWith("typeblitz-cache")) {
            return caches.delete(cacheName);
          }
          return Promise.resolve();
        })
      );
      await self.clients.claim();
    })()
  );
});

// Allow manual skip waiting from the client
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
