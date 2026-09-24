// Voice replies — reads the assistant's messages aloud via the backend TTS
// proxy (Fish Audio). A module singleton because exactly one thing may speak
// at a time and every part of the UI (toggle, chat controller) needs access
// to the same player state.
//
//   setEnabled(bool)  master switch; turning it off also stops playback
//   speak(message)    synthesize + play one assistant message (auto-read)
//   stop()            stop playback and any in-flight request
//   isOn()            current master-switch state
//
// Text preparation (markdown stripping, emoji -> emotion cues) lives in
// lib/speech.js. Each speakable line becomes its own TTS request so the voice
// leaves real space between paragraphs: playback streams chunk by chunk (POST
// /api/v1/tts returns a job id, the job's GET endpoint feeds an <audio>
// element, which starts playing while the server is still generating audio),
// chunks are separated by PARAGRAPH_PAUSE_MS of silence, and the next chunk is
// prefetched while the current one plays so the gap is the intended pause
// rather than API latency.

import { speechParagraphs } from './speech'

let enabled = false
let audio = null
let pendingNext = null
let abortPlay = null
let fetchId = 0

const PARAGRAPH_PAUSE_MS = 350

const ttsApi = {
  setEnabled(value) {
    enabled = value
    if (!value) stop()
  },

  isOn() {
    return enabled
  },

  stop,
  speak,
}

export default ttsApi

function stop() {
  fetchId += 1
  abortPlay?.()
  abortPlay = null
  if (audio) {
    teardown(audio)
    audio = null
  }
  const pending = pendingNext
  pendingNext = null
  // A prefetch that resolves after this point must not keep downloading.
  if (pending) pending.then((player) => teardown(player))
}

function teardown(player) {
  if (!player) return
  player.pause()
  player.removeAttribute('src')
  player.load()
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function startJob(text) {
  const res = await fetch('/api/v1/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ text }),
  })
  if (!res.ok) throw new Error(`tts start failed: ${res.status}`)
  const { id } = await res.json()
  return `/api/v1/tts/${id}`
}

// Prepares one paragraph's player ahead of its turn. Resolves to null when the
// job fails or a newer speak()/stop() has superseded this queue.
function prepare(text, id) {
  return (async () => {
    try {
      const src = await startJob(text)
      if (id !== fetchId) return null
      const player = new Audio(src)
      player.preload = 'auto'
      return player
    } catch (err) {
      console.warn('TTS unavailable:', err?.message ?? err)
      return null
    }
  })()
}

// Resolves true when the clip played to its end, false on an error, an autoplay
// refusal, or an abort from stop().
function playThrough(player) {
  return new Promise((resolve) => {
    let settled = false
    const done = (ok) => {
      if (settled) return
      settled = true
      player.removeEventListener('ended', onEnded)
      player.removeEventListener('error', onError)
      if (abortPlay === done) abortPlay = null
      resolve(ok)
    }
    const onEnded = () => done(true)
    const onError = () => done(false)
    abortPlay = () => done(false)
    player.addEventListener('ended', onEnded)
    player.addEventListener('error', onError)
    try {
      // Autoplay policies can refuse without a recent user gesture; the
      // interaction that produced the reply usually counts as one.
      player.play().catch(() => done(false))
    } catch {
      done(false)
    }
  })
}

export async function speak(message) {
  if (!enabled) return
  const paragraphs = speechParagraphs(message)
  if (!paragraphs.length) return
  stop()
  const id = ++fetchId

  let upcoming = prepare(paragraphs[0], id)
  for (let i = 0; i < paragraphs.length; i += 1) {
    if (id !== fetchId) return
    pendingNext = upcoming
    let player = null
    try {
      player = await upcoming
    } finally {
      pendingNext = null
    }
    if (id !== fetchId) {
      teardown(player)
      return
    }

    const hasNext = i + 1 < paragraphs.length
    if (hasNext) upcoming = prepare(paragraphs[i + 1], id)

    // A failed paragraph is skipped; the rest still get read.
    if (!player) continue

    audio = player
    const played = await playThrough(player)
    audio = null
    if (id !== fetchId) return
    if (!played) continue

    if (!hasNext) return
    await sleep(PARAGRAPH_PAUSE_MS)
    if (id !== fetchId) return
  }
}
