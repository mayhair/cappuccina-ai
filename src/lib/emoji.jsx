import { parse } from '@twemoji/parser'

// Swaps emoji for Twemoji SVGs so flags and other glyphs render on every
// platform (Windows ships no flag emoji). Renders to elements, never innerHTML,
// so untrusted model output stays injection-safe.
export function renderEmoji(text, prefix = 'emoji') {
  if (!text) return []
  const entities = parse(text)
  if (!entities.length) return [text]
  const out = []
  let last = 0
  let n = 0
  for (const e of entities) {
    if (e.indices[0] > last) out.push(text.slice(last, e.indices[0]))
    out.push(
      <img
        key={`${prefix}-${n++}`}
        className="emoji"
        src={e.url}
        alt={e.text}
        draggable={false}
        loading="lazy"
      />,
    )
    last = e.indices[1]
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function EmojiText({ text, prefix }) {
  if (!text) return null
  return <>{renderEmoji(text, prefix)}</>
}
