import { useSyncExternalStore } from 'react'

// A tiny History API router: the URL is the source of truth for which chat,
// canvas and settings section are showing.
//   /                      a fresh new chat (landing page when signed out)
//   /c/:chatId             that chat
//   /c/:chatId/page        that chat with the webpage canvas open
//   ?settings=<section>    Settings dialog open on that section (empty = index)

const CHANGE_EVENT = 'locationchange'
const RETURN_KEY = 'cappuccina-return-to'

// `history.pushState` fires no event, so navigate() announces its own.
export function navigate(to, { replace = false, state } = {}) {
  const url = new URL(to, window.location.origin)
  const next = url.pathname + url.search + url.hash
  const current = window.location.pathname + window.location.search + window.location.hash
  if (next === current) return
  // A replace keeps the entry's flags (e.g. "we pushed this") unless told otherwise.
  const entryState = state !== undefined ? state : replace ? window.history.state : null
  if (replace) window.history.replaceState(entryState, '', next)
  else window.history.pushState(entryState, '', next)
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

let snapshot = null

function getSnapshot() {
  const { pathname, search, href } = window.location
  // Same object while the URL is unchanged, so useSyncExternalStore stays quiet.
  if (!snapshot || snapshot.href !== href) snapshot = { href, pathname, search }
  return snapshot
}

function subscribe(onChange) {
  window.addEventListener('popstate', onChange)
  window.addEventListener(CHANGE_EVENT, onChange)
  return () => {
    window.removeEventListener('popstate', onChange)
    window.removeEventListener(CHANGE_EVENT, onChange)
  }
}

export function useLocation() {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

// { chatId, canvas, settings }. Unknown paths read as the root. `settings` is
// null when the dialog is closed and '' when it is open on its default screen.
export function parseRoute({ pathname, search }) {
  const settings = new URLSearchParams(search).get('settings')
  const match = /^\/c\/([^/]+)(\/page)?\/?$/.exec(pathname)
  if (!match) return { chatId: null, canvas: false, settings }
  let chatId = match[1]
  try {
    chatId = decodeURIComponent(chatId)
  } catch {
    // keep the raw segment
  }
  return { chatId, canvas: Boolean(match[2]), settings }
}

export function currentRoute() {
  return parseRoute(window.location)
}

export function buildUrl({ chatId = null, canvas = false, settings = null } = {}) {
  const path = chatId ? `/c/${encodeURIComponent(chatId)}${canvas ? '/page' : ''}` : '/'
  return settings == null ? path : `${path}?settings=${encodeURIComponent(settings)}`
}

// Where to land after sign-in or an OAuth round trip (the redirect always
// comes back to "/"). Kept per tab; storage can be unavailable.
export function saveReturnTo(path) {
  try {
    sessionStorage.setItem(RETURN_KEY, JSON.stringify({ path, at: Date.now() }))
  } catch {
    // storage unavailable
  }
}

// An abandoned sign-in trip must not hijack a much later reload, so entries
// expire.
const RETURN_TTL_MS = 15 * 60 * 1000

export function takeReturnTo() {
  try {
    const raw = sessionStorage.getItem(RETURN_KEY)
    sessionStorage.removeItem(RETURN_KEY)
    if (!raw) return null
    const { path, at } = JSON.parse(raw)
    if (typeof path !== 'string' || typeof at !== 'number') return null
    if (Date.now() - at > RETURN_TTL_MS) return null
    // Same-origin paths only.
    return path.startsWith('/') && !path.startsWith('//') ? path : null
  } catch {
    // storage unavailable or malformed
    return null
  }
}
