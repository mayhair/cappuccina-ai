// Webpage environment boundary — the chat's canvas (see backend/app/main.py).
// The file text stays server-side and is fetched on demand; messages only carry
// a small {"type": "webpage", webpageId, files} part, matching the way image
// bytes are referenced by id instead of travelling with the history.
// Iterations are addressed by versionId: the canvas lists them, activates one,
// diffs one on demand, and saves a manual edit against the one it rendered.

async function requestJson(url, init) {
  const res = await fetch(url, { credentials: 'include', ...init })
  if (!res.ok) {
    let detail = `request failed: ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // not JSON (or empty body): keep the generic message
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

function jsonBody(payload) {
  return {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }
}

// The active iteration's working copy plus the id it is stored under.
export function getWebpageEnv(chatId) {
  return requestJson(`/api/v1/webpages/${chatId}`)
}

// {activeVersionId, versions: [{id, seq, parentVersionId, origin, label, summary, createdAt}]}
export function getWebpageVersions(chatId) {
  return requestJson(`/api/v1/webpages/${chatId}/versions`)
}

// Activate an iteration; returns the env shape ({webpageId, files, updatedAt, activeVersionId}).
export function activateWebpageVersion(chatId, versionId) {
  return requestJson(`/api/v1/webpages/${chatId}/active`, jsonBody({ versionId }))
}

// Save a hand-edited file as its own iteration. baseVersionId is the iteration
// the tab rendered: a stale value is refused with a 409.
export function saveWebpageFile(chatId, { path, content, baseVersionId }) {
  return requestJson(
    `/api/v1/webpages/${chatId}/files`,
    jsonBody({ path, content, baseVersionId }),
  )
}

// Unified diff of one iteration against its parent (or an explicit base).
export function getWebpageVersionDiff(chatId, versionId, against) {
  const query = against ? `?against=${encodeURIComponent(against)}` : ''
  return requestJson(`/api/v1/webpages/${chatId}/versions/${versionId}/diff${query}`)
}

// One folder asset's bytes as a data URL, for inlining into the preview: the
// sandboxed iframe has no session, so it cannot fetch the asset route itself.
// Asset paths may be nested, so each segment is encoded on its own.
export async function getWebpageAssetDataUrl(chatId, path) {
  const encoded = String(path).split('/').map(encodeURIComponent).join('/')
  const res = await fetch(`/api/v1/webpages/${chatId}/assets/${encoded}`, {
    credentials: 'include',
  })
  if (!res.ok) throw new Error(`asset failed: ${res.status}`)
  const blob = await res.blob()
  return await new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('asset read failed'))
    reader.readAsDataURL(blob)
  })
}
