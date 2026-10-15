// Cappuccina service worker. Hand-written, no libraries.
// Bump VERSION when the caching rules change. Old caches are deleted on activate.
const VERSION = 'v1'
const CACHE = `cappuccina-${VERSION}`

// Only the offline fallback and the icons are precached. The app shell is not,
// so a deploy never leaves users on stale HTML.
const PRECACHE = [
  '/offline.html',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/maskable-512.png',
  '/icons/apple-touch-icon.png',
]

const STATIC_PREFIXES = ['/assets/', '/icons/', '/plates/']
const FONT_RE = /\.(woff2?|ttf|otf)$/i

self.addEventListener('install', (event) => {
  // Activate as soon as the precache is done, instead of waiting for old tabs to close.
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith('cappuccina-') && key !== CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  const url = new URL(req.url)

  // Only same-origin GETs are handled. Anything else goes straight to the network.
  if (req.method !== 'GET' || url.origin !== self.location.origin) return

  // Never touch the API. Chat streams over SSE and relies on cookie auth,
  // so caching or rewriting these responses would break it.
  if (url.pathname.startsWith('/api/')) return

  if (req.mode === 'navigate') {
    event.respondWith(networkFirstPage(req))
    return
  }

  if (STATIC_PREFIXES.some((p) => url.pathname.startsWith(p)) || FONT_RE.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(event))
  }
})

// Pages: try the network. If it fails (offline), show the cached offline page.
async function networkFirstPage(req) {
  try {
    return await fetch(req)
  } catch {
    const cache = await caches.open(CACHE)
    return (await cache.match('/offline.html')) || Response.error()
  }
}

// Static files: answer from cache at once, refresh the copy in the background.
// Hashed bundles never change, so the refresh is a no-op for them. Non-hashed
// files (plates, icons) pick up edits on the next load.
function staleWhileRevalidate(event) {
  return caches.open(CACHE).then(async (cache) => {
    const cached = await cache.match(event.request)
    const refresh = fetch(event.request).then((res) => {
      if (res.ok) cache.put(event.request, res.clone())
      return res
    })
    // Keep the worker alive until the background refresh finishes.
    event.waitUntil(refresh.catch(() => {}))
    return cached || refresh
  })
}
