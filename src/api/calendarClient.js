// Calendar boundary — talks to the FastAPI backend (see backend/app/main.py).
// Connecting Calendar is a full-page redirect to Google (incremental consent);
// everything else is JSON over the session cookie.

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

export const connectCalendarUrl = '/api/v1/calendar/connect'

export const calendarStatus = () => request('/api/v1/calendar/status')

export const disconnectCalendar = () =>
  request('/api/v1/calendar/disconnect', { method: 'POST' })

export const deleteEvent = (eventId) =>
  request(`/api/v1/calendar/events/${encodeURIComponent(eventId)}/delete`, {
    method: 'POST',
  })
