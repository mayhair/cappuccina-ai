// Conversation boundary — talks to the FastAPI backend (see backend/app/main.py).
// POST /api/v1/chat            -> { role, content }
// POST /api/v1/chat/stream SSE -> event: start | token {content} | done

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

export async function sendStreaming({ chatId, messages, reasoning, mode, subject }, onToken) {
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
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      const event = block.match(/^event: (.+)$/m)?.[1]
      const data = block.match(/^data: (.+)$/m)?.[1]
      if (event === 'token' && data) {
        const word = JSON.parse(data).content
        full += word
        onToken(word)
      }
    }
  }
  return { role: 'assistant', content: full }
}
