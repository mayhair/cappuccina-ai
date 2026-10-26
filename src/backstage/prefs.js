import { useSyncExternalStore } from 'react'

// Backstage's sound and motion choices, kept per device like the app's theme.
// A small external store, so the sound engine (outside React) reads the same
// values the settings strip shows.

const STORAGE_KEY = 'cappuccina-backstage'
const REDUCE_QUERY = '(prefers-reduced-motion: reduce)'

const DEFAULTS = {
  sound: true,
  volume: 0.7,
  haptics: true,
  // null follows the device's reduced-motion setting until the user picks.
  calm: null,
}

function load() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY))
    if (saved && typeof saved === 'object') return { ...DEFAULTS, ...saved }
  } catch {
    // storage unavailable or malformed
  }
  return { ...DEFAULTS }
}

const reduceMedia = typeof window !== 'undefined' ? window.matchMedia?.(REDUCE_QUERY) : null

let stored = load()
let snapshot = resolve()
const listeners = new Set()

function resolve() {
  return { ...stored, calm: stored.calm ?? Boolean(reduceMedia?.matches) }
}

function emit() {
  snapshot = resolve()
  for (const listener of listeners) listener()
}

reduceMedia?.addEventListener?.('change', emit)

export function getPrefs() {
  return snapshot
}

export function setPref(key, value) {
  stored = { ...stored, [key]: value }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  } catch {
    // storage unavailable: the choice still applies until the page closes
  }
  emit()
}

export function subscribePrefs(listener) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function usePrefs() {
  return useSyncExternalStore(subscribePrefs, getPrefs, getPrefs)
}

export const canVibrate = typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function'

// A short buzz on phones that support it, when the user hasn't turned it off.
export function buzz(ms = 8) {
  if (canVibrate && snapshot.haptics) navigator.vibrate(snapshot.calm ? Math.ceil(ms / 2) : ms)
}

// Read and write a toy's own saved state (a beat, a pop count). Never throws.
export function loadLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(`cappuccina-backstage-${key}`)
    return raw == null ? fallback : JSON.parse(raw)
  } catch {
    return fallback
  }
}

export function saveLocal(key, value) {
  try {
    localStorage.setItem(`cappuccina-backstage-${key}`, JSON.stringify(value))
  } catch {
    // storage unavailable
  }
}
