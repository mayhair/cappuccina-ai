// Install-as-app support: the browser's install prompt, the manifest link and
// the service worker. main.jsx imports this before anything renders, because
// `beforeinstallprompt` can fire before React mounts. Importing the module is
// enough to start listening.
//
//   subscribe(cb)           useSyncExternalStore subscription
//   getSnapshot()           { installed, canPrompt }, same object until a value changes
//   promptInstall()         shows the stored install prompt; resolves to
//                           'accepted' | 'dismissed' | 'unavailable'
//   setManifestEnabled(on)  adds or removes the manifest <link>
//   registerServiceWorker() production only; registers /sw.js
//   isStandalone()          running as an installed app
//   platformHint()          'ios' | 'safari-desktop' | 'firefox' | 'other'
//
// The manifest link is added only while someone is signed in (App.jsx calls
// setManifestEnabled), so the browser offers to install the app only to users
// who have an account.

const MANIFEST_HREF = '/manifest.webmanifest'
const SW_URL = '/sw.js'
const DISPLAY_MODES = ['standalone', 'window-controls-overlay', 'minimal-ui']

let deferredPrompt = null
let appInstalledSeen = false
const listeners = new Set()

export function isStandalone() {
  if (typeof window === 'undefined') return false
  if (window.navigator.standalone === true) return true
  if (!window.matchMedia) return false
  return DISPLAY_MODES.some((mode) => window.matchMedia(`(display-mode: ${mode})`).matches)
}

export function platformHint() {
  if (typeof navigator === 'undefined') return 'other'
  const ua = navigator.userAgent || ''
  // iPadOS 13+ reports itself as a Mac, so touch points tell it apart.
  const isIOS =
    /iPhone|iPad|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  if (isIOS) return 'ios'
  const isAndroid = /Android/.test(ua)
  if (/Firefox\//.test(ua) && !isAndroid) return 'firefox'
  const isMac = /Macintosh/.test(ua)
  const isSafari =
    /Safari\//.test(ua) && !/Chrome\/|Chromium\/|CriOS|Edg\/|EdgA\/|OPR\/|FxiOS|Android/.test(ua)
  if (isMac && isSafari) return 'safari-desktop'
  return 'other'
}

// The snapshot object is replaced only when a value changes, so
// useSyncExternalStore sees the same object between changes and does not loop.
let snapshot = { installed: isStandalone(), canPrompt: false }

function refresh() {
  const installed = appInstalledSeen || isStandalone()
  const canPrompt = deferredPrompt !== null
  if (installed === snapshot.installed && canPrompt === snapshot.canPrompt) return
  snapshot = { installed, canPrompt }
  listeners.forEach((cb) => cb())
}

export function subscribe(callback) {
  listeners.add(callback)
  return () => {
    listeners.delete(callback)
  }
}

export function getSnapshot() {
  return snapshot
}

export async function promptInstall() {
  const event = deferredPrompt
  if (!event) return 'unavailable'
  try {
    await event.prompt()
    const choice = await event.userChoice
    return choice?.outcome === 'accepted' ? 'accepted' : 'dismissed'
  } catch {
    return 'unavailable'
  } finally {
    // A prompt event can be used only once.
    deferredPrompt = null
    refresh()
  }
}

export function setManifestEnabled(enabled) {
  if (typeof document === 'undefined') return
  const links = document.head.querySelectorAll('link[rel="manifest"]')
  if (enabled) {
    if (links.length === 0) {
      const link = document.createElement('link')
      link.rel = 'manifest'
      link.href = MANIFEST_HREF
      document.head.appendChild(link)
    }
  } else {
    links.forEach((link) => link.remove())
  }
}

export function registerServiceWorker() {
  try {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return
    if (!import.meta.env.PROD) {
      // Dev serves fresh modules. A worker left over from a production build
      // would serve stale files, so remove it.
      navigator.serviceWorker
        .getRegistrations()
        .then((registrations) => registrations.forEach((r) => r.unregister()))
        .catch(() => {})
      return
    }
    const register = () => {
      navigator.serviceWorker
        .register(SW_URL, { scope: '/' })
        .catch((err) => console.warn('Service worker registration failed:', err))
    }
    if (document.readyState === 'complete') register()
    else window.addEventListener('load', register, { once: true })
  } catch {
    // Offline support is optional; it must never break the app.
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (event) => {
    // No preventDefault: the browser keeps its own Install signal, and
    // Settings calls prompt() on this event later.
    deferredPrompt = event
    refresh()
  })
  window.addEventListener('appinstalled', () => {
    appInstalledSeen = true
    deferredPrompt = null
    refresh()
  })
  if (window.matchMedia) {
    for (const mode of DISPLAY_MODES) {
      const query = window.matchMedia(`(display-mode: ${mode})`)
      if (query.addEventListener) query.addEventListener('change', refresh)
      else query.addListener(refresh)
    }
  }
}
