// Auth boundary — talks to the FastAPI backend (see backend/app/main.py).
// Session lives in an HttpOnly cookie set by the server; every request just
// needs credentials: 'include'.

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
    if (res.status === 401) {
      const err = new Error(detail)
      err.status = 401
      throw err
    }
    throw new Error(detail)
  }
  return res.json()
}

export const getMe = () => request('/api/v1/auth/me')
export const updateProfile = ({ name }) =>
  request('/api/v1/auth/me', { method: 'PATCH', body: { displayName: name } })
export const setAdultMode = (enabled) =>
  request('/api/v1/auth/me', { method: 'PATCH', body: { adultMode: enabled } })
export const setMemoryEnabled = (enabled) =>
  request('/api/v1/auth/me', { method: 'PATCH', body: { memoryEnabled: enabled } })
export const setStoryChoices = (enabled) =>
  request('/api/v1/auth/me', { method: 'PATCH', body: { storyChoices: enabled } })
// Deletes the account and everything linked to it; the server clears the cookie.
export const deleteAccount = () => request('/api/v1/auth/me', { method: 'DELETE' })

// Sign-in is a full-page redirect (cookie is set by the OAuth callback).
export const googleSignInUrl = '/api/v1/auth/google/start'

export async function logout() {
  await fetch('/api/v1/auth/logout', {
    method: 'POST',
    credentials: 'include',
  })
}
