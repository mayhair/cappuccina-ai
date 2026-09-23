import { parse } from '@twemoji/parser'

// Emoji -> Fish Audio emotion cue. The S2 family reads `[bracket]` cues as
// delivery instructions rather than words, so a sentence-final emoji becomes a
// cue at the sentence's start. Keys are plain emoji; lookup normalizes away the
// variation selector and skin-tone modifiers (❤️ -> ❤, 👍🏽 -> 👍). Emoji with
// no emotional meaning (coffee, ballet shoes, lore characters, flags, objects)
// are not in the map and get stripped instead — the TTS reads raw emoji as
// gibberish.
const EMOJI_EMOTION = {
  '😄': 'happy', '😀': 'happy', '😃': 'happy', '😁': 'happy', '🙂': 'happy', '😊': 'happy',
  '🥰': 'warm', '😍': 'warm', '🤗': 'warm', '❤': 'warm',
  '💕': 'warm', '💖': 'warm', '💗': 'warm', '💓': 'warm', '💞': 'warm',
  '😂': 'laughing', '🤣': 'laughing', '😆': 'laughing', '😹': 'laughing', '😸': 'laughing',
  '🎉': 'excited', '🎊': 'excited', '🥳': 'excited', '🔥': 'excited', '⚡': 'excited',
  '✨': 'delighted', '🌟': 'delighted', '⭐': 'delighted', '💫': 'delighted',
  '😭': 'sad', '😢': 'sad', '😔': 'sad', '😞': 'sad', '🥺': 'sad', '💔': 'sad',
  '😅': 'nervous', '😬': 'nervous', '😰': 'nervous', '😓': 'nervous', '🫠': 'nervous',
  '😮': 'surprised', '😲': 'surprised', '😯': 'surprised', '🤯': 'surprised', '😱': 'surprised',
  '👀': 'curious', '🤔': 'curious', '🧐': 'curious',
  '😉': 'playful', '😏': 'playful', '😜': 'playful', '😈': 'playful', '🥸': 'playful', '😋': 'playful',
  '😎': 'confident', '💪': 'confident',
  '🙏': 'grateful',
  '🙈': 'embarrassed',
  '😌': 'calm',
  '😡': 'angry', '😠': 'angry', '😤': 'angry',
  '👍': 'satisfied', '👌': 'satisfied',
}

const MAX_CUES_PER_SENTENCE = 2

function normalizeEmoji(text) {
  return text
    .replace(/[\uFE0E\uFE0F]/g, '')
    .replace(/[\u{1F3FB}-\u{1F3FF}]/gu, '')
}

function uniqueCues(cues) {
  const out = []
  for (const cue of cues) {
    if (!out.includes(cue)) out.push(cue)
  }
  return out.slice(0, MAX_CUES_PER_SENTENCE)
}

// Walks one sentence's emoji. Emoji at the very start belong to the previous
// sentence (they trail it visually: "I'm sad. 😭 What next?"), the rest belong
// to this one. Returns the sentence with its emoji removed plus both cue lists.
function splitChunkEmoji(chunk, hasPrevious) {
  let entities = []
  try {
    entities = parse(chunk)
  } catch {
    return { text: chunk, own: [], leading: [] }
  }
  if (!entities.length) return { text: chunk, own: [], leading: [] }

  let text = ''
  let cursor = 0
  let atStart = true
  const own = []
  const leading = []
  for (const entity of entities) {
    const [start, end] = entity.indices
    const between = chunk.slice(cursor, start)
    if (between.trim()) atStart = false
    const cue = EMOJI_EMOTION[normalizeEmoji(entity.text)]
    if (cue) {
      if (atStart && hasPrevious) leading.push(cue)
      else own.push(cue)
    }
    text += between
    cursor = end
  }
  text += chunk.slice(cursor)
  return { text, own, leading }
}

// Converts emoji to emotion cues and drops the rest. Emoji detection reuses the
// same parser that renders them, which also means math glyphs like ° are left
// alone.
function applyEmotionTags(text) {
  const chunks = text.split(/(?<=[.!?…])\s+|\n+/)
  const parts = chunks.map((chunk) => ({ text: chunk, cues: [] }))

  parts.forEach((part, i) => {
    const { text: stripped, own, leading } = splitChunkEmoji(part.text, i > 0)
    part.text = stripped
    part.cues.push(...own)
    if (leading.length && i > 0) parts[i - 1].cues.push(...leading)
  })

  // Nothing but emoji is not something to read aloud.
  if (!parts.some((part) => /\p{L}|\p{N}/u.test(part.text))) return ''

  return parts
    .map((part) => {
      const cues = uniqueCues(part.cues)
      // Cues stack visually but a later cue replaces an earlier one, so keep
      // them in a single bracket.
      const prefix = cues.length ? `[${cues.join(', ')}] ` : ''
      return `${prefix}${part.text.trim()}`
    })
    .join(' ')
    .replace(/\s+([.,!?;:…])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function stripMarkdown(raw) {
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
}

// Plain, speakable text for one assistant message: markdown and LaTeX removed,
// emoji turned into emotion cues (or stripped). Reasoning/tool parts are
// filtered by the caller.
export function speechText(message) {
  const raw = (message.parts ?? [])
    .filter((part) => part.type === 'text')
    .map((part) => part.text)
    .join('\n\n')
  if (!raw.trim()) return ''
  return applyEmotionTags(stripMarkdown(raw))
}
