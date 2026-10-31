// Auth boundary — talks to the FastAPI backend (see backend/app/main.py).
// Session lives in an HttpOnly cookie set by the server; every request just
// needs credentials: 'include'.

import { request } from './http'

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

// Email + password. `lang` picks the language of the emails the server sends.
// Register, resend and forgot answer {status: 'check_email'} whether or not the
// address has an account; confirm, login and reset set the session cookie and
// return the user.
export const register = ({ email, password, name, turnstileToken, lang }) =>
  request('/api/v1/auth/register', {
    method: 'POST',
    body: { email, password, name, turnstileToken, lang },
  })
export const resendConfirmation = ({ email, lang }) =>
  request('/api/v1/auth/resend-confirmation', { method: 'POST', body: { email, lang } })
export const confirmEmail = (token) =>
  request('/api/v1/auth/confirm', { method: 'POST', body: { token } })
export const login = ({ email, password }) =>
  request('/api/v1/auth/login', { method: 'POST', body: { email, password } })
export const forgotPassword = ({ email, lang }) =>
  request('/api/v1/auth/forgot-password', { method: 'POST', body: { email, lang } })
export const resetPassword = ({ token, newPassword, lang }) =>
  request('/api/v1/auth/reset-password', { method: 'POST', body: { token, newPassword, lang } })
// `currentPassword` is ignored for an account that has no password yet.
export const changePassword = ({ currentPassword, newPassword, lang }) =>
  request('/api/v1/auth/change-password', {
    method: 'POST',
    body: { currentPassword, newPassword, lang },
  })

export async function logout() {
  await fetch('/api/v1/auth/logout', {
    method: 'POST',
    credentials: 'include',
  })
}
