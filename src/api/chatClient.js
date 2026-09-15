// Conversation boundary — talks to the FastAPI backend (see backend/app/main.py).
// POST /api/v1/chat            -> { role, content, reasoning, tool_calls }
// POST /api/v1/chat/stream SSE -> event: start | token {content} | reasoning {content}
//                                 | tool_call {id,name,display,args,label}
//                                 | tool_result {id,name,content,ok} | done

function buildRequest({ chatId, messages, reasoning, mode, subject }) {
  return {
    chatId,
    messages,
    reasoning: reasoning ?? 'medium',
    mode: mode ?? 'chat',
    subject: subject ?? null,
  }
}

export async function send({ chatId, messages, reasoning, mode, subject }) {
  const res = await fetch('/api/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildRequest({ chatId, messages, reasoning, mode, subject })),
  })
  if (!res.ok) throw new Error(`chat failed: ${res.status}`)
  return res.json()
}

// handlers: { onToken, onReasoning, onToolCall, onToolResult }
export async function sendStreaming({ chatId, messages, reasoning, mode, subject }, handlers = {}) {
  const { onToken, onReasoning, onToolCall, onToolResult } = handlers
  const res = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildRequest({ chatId, messages, reasoning, mode, subject })),
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
      }
    }
  }
  return { role: 'assistant', content: full, reasoning: thinking }
}
