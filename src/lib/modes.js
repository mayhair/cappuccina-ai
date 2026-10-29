// Chat modes, in picker order. Matches CHAT_MODES in backend/app/schemas.py.
export const CHAT_MODES = ['chat', 'tutor', 'story']

// Modes this server offers: Story mode can be switched off in its config.
export function availableModes(user) {
  return user?.storyModeEnabled === false ? CHAT_MODES.filter((m) => m !== 'story') : CHAT_MODES
}

// The mode a chat runs in here. A story chat on a server with Story mode off
// carries on as an ordinary chat.
export function effectiveMode(mode, user) {
  const m = normalizeMode(mode)
  return availableModes(user).includes(m) ? m : 'chat'
}

export function normalizeMode(mode) {
  return CHAT_MODES.includes(mode) ? mode : 'chat'
}

// The sidebar tag a mode shows next to the chat title; plain chat has none.
export function modeTag(mode) {
  return mode === 'chat' ? null : mode.toUpperCase()
}

// Story mode's out-of-character marker: "(ooc: …)" steps outside the story.
const OOC_RE = /^\s*\(ooc\b[:)]?\s*/i

export function isOoc(text) {
  return OOC_RE.test(text ?? '')
}

// Her out-of-character replies start with "(ooc)" (see backend lore/story.md).
export function isOocReply(text) {
  return /^\s*\(ooc\)/i.test(text ?? '')
}

export function wrapOoc(text) {
  return isOoc(text) ? text : `(ooc: ${text.trim()})`
}
