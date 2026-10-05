// Webpage environment boundary — the chat's canvas (see backend/app/main.py).
// The file text stays server-side and is fetched on demand; messages only carry
// a small {"type": "webpage", webpageId, files} part, matching the way image
// bytes are referenced by id instead of travelling with the history.

export async function getWebpageEnv(chatId) {
  const res = await fetch(`/api/v1/webpages/${chatId}`, { credentials: 'include' })
  if (!res.ok) throw new Error(`webpage env failed: ${res.status}`)
  return res.json()
}
