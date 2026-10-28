// Shared transport for the API clients: JSON over the session cookie
// (credentials: 'include'), failing with the server's `detail` as the message
// and the HTTP status on `err.status`.

export async function responseError(res, fallback = `request failed: ${res.status}`) {
  let detail = fallback
  try {
    const body = await res.json()
    if (body?.detail) detail = body.detail
  } catch {
    // not JSON (or empty body): keep the fallback message
  }
  const err = new Error(detail)
  err.status = res.status
  return err
}

// `body` is sent as JSON; `form` (a FormData) is sent as-is.
export async function request(path, { method = 'GET', body, form } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    credentials: 'include',
    body: form ?? (body ? JSON.stringify(body) : undefined),
  })
  if (!res.ok) throw await responseError(res)
  return res.json()
}
