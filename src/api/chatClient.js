// Conversation boundary — talks to the FastAPI backend (see backend/app/main.py).
// POST /api/v1/chat            -> { role, content, reasoning, tool_calls }
// POST /api/v1/chat/stream SSE -> event: start | token {content} | reasoning {content}
//                                 | tool_call {id,name,display,args,label}
//                                 | tool_result {id,name,content,ok}
//                                 | image {type,imageId,prompt,width,height}
//                                 | done | title {title} (first message only, after done)

function buildRequest({ chatId, messages, reasoning, mode, subject, generateTitle }) {
  return {
    chatId,
    messages,
    reasoning: reasoning ?? 'high',
    mode: mode ?? 'chat',
    subject: subject ?? null,
    generateTitle: generateTitle ?? false,
  }
}

export async function send({ chatId, messages, reasoning, mode, subject }) {
  const res = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(buildRequest({ chatId, messages, reasoning, mode, subject })),
  })
  if (!res.ok) throw new Error(`chat failed: ${res.status}`)
  return res.json()
}

// handlers: { onToken, onReasoning, onToolCall, onToolResult, onImage, onTitle, onDone }
export async function sendStreaming({ chatId, messages, reasoning, mode, subject, generateTitle }, handlers = {}) {
  const { onToken, onReasoning, onToolCall, onToolResult, onImage, onTitle, onDone } = handlers
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(buildRequest({ chatId, messages, reasoning, mode, subject, generateTitle })),
  })
  if (!res.ok || !res.body) throw new Error(`chat stream failed: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let full = ''
  let thinking = ''
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
        const word = payload.content ?? ''
        full += word
        onToken?.(word)
      } else if (event === 'reasoning') {
        const piece = payload.content ?? ''
        thinking += piece
        onReasoning?.(piece)
      } else if (event === 'tool_call') {
        onToolCall?.(payload)
      } else if (event === 'tool_result') {
        onToolResult?.(payload)
      } else if (event === 'image') {
        onImage?.(payload)
      } else if (event === 'title') {
        onTitle?.(payload.title ?? '')
      }
    }
  }
  return { role: 'assistant', content: full, reasoning: thinking }
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
