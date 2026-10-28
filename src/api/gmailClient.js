// Gmail boundary — talks to the FastAPI backend (see backend/app/main.py).
// Connecting Gmail is a full-page redirect to Google (incremental consent);
// everything else is JSON over the session cookie.

import { request } from './http'

export const connectGmailUrl = '/api/v1/gmail/connect'

export const gmailStatus = () => request('/api/v1/gmail/status')

export const disconnectGmail = () =>
  request('/api/v1/gmail/disconnect', { method: 'POST' })

export const updateDraft = (draftId, { to, subject, body }) =>
  request(`/api/v1/gmail/drafts/${encodeURIComponent(draftId)}`, {
    method: 'PATCH',
    body: { to, subject, body },
  })

export const sendDraft = (draftId) =>
  request(`/api/v1/gmail/drafts/${encodeURIComponent(draftId)}/send`, {
    method: 'POST',
  })
