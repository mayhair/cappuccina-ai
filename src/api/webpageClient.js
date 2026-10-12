// Webpage workspace boundary — the chat's canvas (see backend/app/main.py).
// The workspace lives server-side: messages carry only a small
// {"type": "webpage", webpageId, files} part, the canvas fetches one file's
// text at a time, and the preview iframe loads the served preview URL (a
// sandboxed, session-less document, so the picture URLs the Code tab shows
// come from previewBase — a capability URL scoped to the chat).
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

// The chat's environment — {webpageId, files (names), updatedAt,
// activeVersionId, previewUrl, previewBase, entry, serveRoot, hasBuild,
// lastBuild}. No site yet: webpageId null and an empty file list.
export function getWebpageEnv(chatId) {
  return requestJson(`/api/v1/webpages/${chatId}`)
}

// One file on demand: text is {path, content}, a picture or other binary is
// {path, binary: true, mime, size}. Paths may be nested, so each segment is
// encoded on its own and the API's :path converter rebuilds them.
export function getWebpageFile(chatId, path) {
  const encoded = String(path).split('/').map(encodeURIComponent).join('/')
  return requestJson(`/api/v1/webpages/${chatId}/files/${encoded}`)
}

// {activeVersionId, versions: [{id, seq, parentVersionId, origin, label, summary, createdAt}]}
export function getWebpageVersions(chatId) {
  return requestJson(`/api/v1/webpages/${chatId}/versions`)
}

// Activate an iteration; returns the env shape.
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

// The whole site as a zip, for rehosting or sending: {blob, filename}.
export async function downloadWebpageZip(chatId) {
  const res = await fetch(`/api/v1/webpages/${chatId}/download`, { credentials: 'include' })
  if (!res.ok) {
    let detail = `request failed: ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // not JSON: keep the generic message
    }
    throw new Error(detail)
  }
  const named = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') ?? '')
  return { blob: await res.blob(), filename: named?.[1] ?? 'website.zip' }
}

// A workspace file's served URL, for the Code tab's <img>: the preview route
// carries the chat's capability key instead of a session cookie, so it works
// inside a sandboxed frame and outside one alike.
export function previewFileUrl(webpage, path) {
  const base = webpage?.previewBase ?? ''
  const encoded = String(path ?? '').split('/').map(encodeURIComponent).join('/')
  return `${base}${encoded}`
}
