// Story mode boundary — talks to the FastAPI backend (see backend/app/main.py).
// The cast picker reads the lore book; the "Story so far" panel edits the
// chapter summaries the server writes as a story grows. The suggested-actions
// switch lives on the user (see setStoryChoices in authClient.js).

async function request(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    credentials: 'include',
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = `request failed: ${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // non-JSON error response
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

// -> [{ id, name, blurb, hasLook }]
export const listCharacters = () => request('/api/v1/lore/characters')

// The user's own character library.
// -> { characters: [{ id, name, description, voice, createdAt, updatedAt }], limit, nameMax, textMax }
export const listMyCharacters = () => request('/api/v1/story/characters')

export const createMyCharacter = ({ name, description, voice }) =>
  request('/api/v1/story/characters', { method: 'POST', body: { name, description, voice } })

export const updateMyCharacter = (id, { name, description, voice }) =>
  request(`/api/v1/story/characters/${id}`, { method: 'PATCH', body: { name, description, voice } })

export const deleteMyCharacter = (id) =>
  request(`/api/v1/story/characters/${id}`, { method: 'DELETE' })

const chapters = (chatId) => `/api/v1/chats/${encodeURIComponent(chatId)}/story/chapters`

// -> { chapters: [{ id, startUid, endUid, text, edited, createdAt, updatedAt }], maxChars, chapterSize }
export const listChapters = (chatId) => request(chapters(chatId))

export const createChapter = (chatId, text) =>
  request(chapters(chatId), { method: 'POST', body: { text } })

export const updateChapter = (chatId, id, text) =>
  request(`${chapters(chatId)}/${id}`, { method: 'PATCH', body: { text } })

export const deleteChapter = (chatId, id) =>
  request(`${chapters(chatId)}/${id}`, { method: 'DELETE' })

export const resummarizeChapter = (chatId, id) =>
  request(`${chapters(chatId)}/${id}/resummarize`, { method: 'POST' })
