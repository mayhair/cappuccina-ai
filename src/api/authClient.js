// Auth boundary — talks to the FastAPI backend (see backend/app/main.py).
// Session lives in an HttpOnly cookie set by the server; every request just
// needs credentials: 'include'.

async function request(path, body) {
  const res = await fetch(path, {
    method: body ? 'POST' : 'GET',
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
export const register = ({ email, password, name }) =>
  request('/api/v1/auth/register', { email, password, displayName: name })
export const login = ({ email, password }) =>
  request('/api/v1/auth/login', { email, password })

export async function logout() {
  await fetch('/api/v1/auth/logout', {
    method: 'POST',
    credentials: 'include',
  })
}
