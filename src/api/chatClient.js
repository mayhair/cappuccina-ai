// Conversation boundary — talks to the FastAPI backend (see backend/app/main.py).
// POST /api/v1/chat/stream SSE -> event: start | token {content} | reasoning {content}
//                                 | tool_call {id,name,display,args,label}
//                                 | tool_result {id,name,content,ok}
//                                 | image {type,imageId,prompt,width,height}
//                                 | webpage {webpageId,path,files,content}
//                                 | done | title {title} (first message only, after done)

function buildRequest({ chatId, messages, tree, replyId, reasoning, mode, subject, generateTitle }) {
  return {
    chatId,
    messages,
    tree: tree ?? [],
    replyId: replyId ?? null,
    reasoning: reasoning ?? 'high',
    mode: mode ?? 'chat',
    subject: subject ?? null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    generateTitle: generateTitle ?? false,
  }
}

// handlers: { onToken, onReasoning, onToolCall, onToolResult, onImage, onWebpage, onTitle, onDone }
export async function sendStreaming({ chatId, messages, tree, replyId, reasoning, mode, subject, generateTitle }, handlers = {}) {
  const { onToken, onReasoning, onToolCall, onToolResult, onImage, onWebpage, onTitle, onDone } = handlers
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(buildRequest({ chatId, messages, tree, replyId, reasoning, mode, subject, generateTitle })),
  })
  if (!res.ok || !res.body) throw new Error(`chat stream failed: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = buffer.replace(/\r\n/g, '\n')
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      const event = block.match(/^event: (.+)$/m)?.[1]
      const data = block.match(/^data: (.+)$/m)?.[1]
      if (event === 'done') {
        onDone?.()
        continue
      }
      if (!data || data === '{}') continue
      const payload = JSON.parse(data)
      if (event === 'token') {
        onToken?.(payload.content ?? '')
      } else if (event === 'reasoning') {
        onReasoning?.(payload.content ?? '')
      } else if (event === 'tool_call') {
        onToolCall?.(payload)
      } else if (event === 'tool_result') {
        onToolResult?.(payload)
      } else if (event === 'image') {
        onImage?.(payload)
      } else if (event === 'webpage') {
        onWebpage?.(payload)
      } else if (event === 'title') {
        onTitle?.(payload.title ?? '')
      }
    }
  }
}

// ---------- chat CRUD (server-side storage) ----------

async function jsonFetch(url, options = {}) {
  const res = await fetch(url, { credentials: 'include', ...options })
  if (!res.ok) throw new Error(`${url} failed: ${res.status}`)
  return res.json()
}

export const listChats = () => jsonFetch('/api/v1/chats')

export const createChatOnServer = (mode = 'chat', subject = null) =>
  jsonFetch('/api/v1/chats', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, subject }),
  })

export const renameChatOnServer = (chatId, title) =>
  jsonFetch(`/api/v1/chats/${chatId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })

export const deleteChatOnServer = (chatId) =>
  jsonFetch(`/api/v1/chats/${chatId}`, { method: 'DELETE' })

// Remember which branch tail the user is viewing (follows them across devices).
export const setActiveBranchOnServer = (chatId, leafId) =>
  jsonFetch(`/api/v1/chats/${chatId}/active-branch`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ leafId: leafId ?? null }),
  })

// ---------- attachments (user uploads) ----------

export async function uploadAttachment(chatId, file) {
  const form = new FormData()
  form.append('chatId', chatId)
  form.append('file', file)
  const res = await fetch('/api/v1/attachments', {
    method: 'POST',
    credentials: 'include',
    body: form,
  })
  if (!res.ok) {
    let detail = `Upload failed (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // non-JSON error body
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }
  return res.json()
}

export const deleteAttachment = (attachmentId) =>
  jsonFetch(`/api/v1/attachments/${attachmentId}`, { method: 'DELETE' })
