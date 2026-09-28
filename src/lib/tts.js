// Voice replies — reads the assistant's messages aloud via the backend TTS
// proxy (Fish Audio). A module singleton because exactly one thing may speak
// at a time and every part of the UI (toggle, chat controller) needs access
// to the same player state.
//
//   setEnabled(bool)  master switch; turning it off also stops playback
//   enqueue(message)  queue one finished bubble to be read aloud (auto-read)
//   stop()            stop playback, clear the queue, abort in-flight requests
//   isOn()            current master-switch state
//
// Text preparation (markdown stripping, em/en dashes -> semicolons, emoji ->
// emotion cues) lives in lib/speech.js. Each speakable line becomes its own
// TTS request so the voice leaves real space between paragraphs: playback
// streams clip by clip (POST /api/v1/tts returns a job id, the job's GET
// endpoint feeds an <audio> element, which starts playing while the server is
// still generating audio), the lines of one bubble are separated by
// PARAGRAPH_PAUSE_MS of silence, and the next clip is prefetched while the
// current one plays so the gap is the intended pause rather than API latency.
// Bubbles are enqueued the moment they finish and follow each other back to
// back — a bubble never waits for the rest of the run to complete.

import { speechParagraphs } from './speech'

let enabled = false
let audio = null
let pendingNext = null
let prefetch = null
let abortPlay = null
let fetchId = 0
// Clips waiting to play, in order: { text, gap } with gap = silence before it.
let queue = []
let draining = false

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
  enqueue,
}

export default ttsApi

function stop() {
  fetchId += 1
  draining = false
  queue = []
  abortPlay?.()
  abortPlay = null
  if (audio) {
    teardown(audio)
    audio = null
  }
  const pending = pendingNext
  pendingNext = null
  const ahead = prefetch
  prefetch = null
  // Players created after this point must not keep downloading.
  if (pending) pending.then((player) => teardown(player))
  if (ahead) ahead.promise.then((player) => teardown(player))
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

// Prepares one clip's player ahead of its turn. Resolves to null when the job
// fails or a newer enqueue/stop cycle has superseded this queue.
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

// Starts downloading the next queued clip while the current one plays, so
// paragraphs and bubbles follow each other without API-latency gaps.
function kickPrefetch(id) {
  if (prefetch) return
  const next = queue[0]
  if (!next) return
  prefetch = { item: next, promise: prepare(next.text, id) }
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

// Queues one finished bubble to be read aloud. Its lines are separated by
// PARAGRAPH_PAUSE_MS; consecutive bubbles play back to back.
function enqueue(message) {
  if (!enabled) return
  const paragraphs = speechParagraphs(message)
  if (!paragraphs.length) return
  paragraphs.forEach((text, i) => {
    queue.push({ text, gap: i === 0 ? 0 : PARAGRAPH_PAUSE_MS })
  })
  kickPrefetch(fetchId)
  void drain()
}

async function drain() {
  if (draining) return
  draining = true
  const id = fetchId
  try {
    while (queue.length) {
      if (id !== fetchId) return
      const item = queue.shift()
      if (item.gap) {
        await sleep(item.gap)
        if (id !== fetchId) return
      }

      let prep
      if (prefetch && prefetch.item === item) {
        prep = prefetch.promise
      } else {
        if (prefetch) prefetch.promise.then((player) => teardown(player))
        prep = prepare(item.text, id)
      }
      prefetch = null
      pendingNext = prep
      let player = null
      try {
        player = await prep
      } finally {
        if (pendingNext === prep) pendingNext = null
      }
      if (id !== fetchId) {
        teardown(player)
        return
      }

      kickPrefetch(id)

      // A failed clip is skipped; the rest still get read.
      if (!player) continue

      audio = player
      const played = await playThrough(player)
      if (audio === player) audio = null
      if (id !== fetchId) return
      if (!played) continue
    }
  } finally {
    if (id === fetchId) draining = false
  }
}
