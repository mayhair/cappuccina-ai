// Voice replies — reads the assistant's messages aloud via the backend TTS
// proxy (Fish Audio). A module singleton because exactly one thing may speak
// at a time and every part of the UI (toggle, chat controller) needs access
// to the same player state.
//
//   setEnabled(bool)  master switch; turning it off also stops playback
//   speak(message)    synthesize + play one assistant message (auto-read)
//   stop()            stop playback and any in-flight request
//   isOn()            current master-switch state
//   speechText(m)     plain-text version of a message, for speech
//
// Playback streams: POST /api/v1/tts returns a job id, the job's GET endpoint
// feeds an <audio> element, which starts playing while the server is still
// generating audio.

let enabled = false
let audio = null
let fetchId = 0

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
  if (audio) {
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
    audio = null
  }
}

// Strips what should not be spoken: code blocks, images, LaTeX and markdown
// decoration. Reasoning/tool parts are already filtered by the caller.
function speechText(message) {
  const raw = (message.parts ?? [])
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('\n\n')
  if (!raw.trim()) return ''
  return raw
    .replace(/```[\s\S]*?(```|$)/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\$\$[\s\S]*?(?:\$\$|$)/g, ' ')
    .replace(/\$([^$\n]*)\$/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/(\*|_)(.*?)\1/g, '$2')
    .replace(/~~(.*?)~~/g, '$1')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
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

export async function speak(message) {
  if (!enabled) return
  const text = speechText(message)
  if (!text) return
  stop()
  const id = ++fetchId
  try {
    const src = await startJob(text)
    if (id !== fetchId) return
    const player = new Audio(src)
    player.addEventListener('error', () => {
      if (audio === player) audio = null
    })
    audio = player
    try {
      await player.play()
    } catch {
      // Autoplay policies can refuse without a recent user gesture; the
      // interaction that produced this reply usually counts as one.
      if (audio === player) audio = null
    }
  } catch (err) {
    console.warn('TTS unavailable:', err?.message ?? err)
  }
}
