// Calendar boundary — talks to the FastAPI backend (see backend/app/main.py).
// Connecting Calendar is a full-page redirect to Google (incremental consent);
// everything else is JSON over the session cookie.

import { request } from './http'

export const connectCalendarUrl = '/api/v1/calendar/connect'

export const calendarStatus = () => request('/api/v1/calendar/status')

export const disconnectCalendar = () =>
  request('/api/v1/calendar/disconnect', { method: 'POST' })

export const deleteEvent = (eventId) =>
  request(`/api/v1/calendar/events/${encodeURIComponent(eventId)}/delete`, {
    method: 'POST',
  })
