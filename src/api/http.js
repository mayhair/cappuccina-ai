// Shared transport for the API clients: JSON over the session cookie
// (credentials: 'include'), failing with the server's `detail` as the message
// and the HTTP status on `err.status`. A `detail` of {code, message} also sets
// `err.code`, which callers can translate.

export async function responseError(res, fallback = `request failed: ${res.status}`) {
  let detail = fallback
  let code
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (body?.detail?.message) ({ message: detail, code } = body.detail)
  } catch {
    // not JSON (or empty body): keep the fallback message
  }
  const err = new Error(detail)
  err.status = res.status
  if (code) err.code = code
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
