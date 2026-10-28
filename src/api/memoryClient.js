// Memory boundary — talks to the FastAPI backend (see backend/app/main.py).
// The AI saves memories through its own tools; these calls back the Memory
// section in Settings and the Undo button on the chat's memory card. The
// on/off switch lives on the user (see setMemoryEnabled in authClient.js).

import { request } from './http'

// -> { memories: [{ id, content, createdAt, updatedAt }], enabled, limit }
export const listMemories = () => request('/api/v1/memories')

export const createMemory = (content) =>
  request('/api/v1/memories', { method: 'POST', body: { content } })

export const updateMemory = (id, content) =>
  request(`/api/v1/memories/${encodeURIComponent(id)}`, { method: 'PATCH', body: { content } })

export const deleteMemory = (id) =>
  request(`/api/v1/memories/${encodeURIComponent(id)}`, { method: 'DELETE' })

export const deleteAllMemories = () => request('/api/v1/memories', { method: 'DELETE' })
