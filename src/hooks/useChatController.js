import { useCallback, useRef, useState } from 'react'
import { sendStreaming } from '../api/chatClient'
import { initialChats } from '../lib/seedChats'

let nextId = 1

export const EFFORTS = [
  { label: 'Light', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
  { label: 'Extra High', value: 'xhigh' },
]

export function useChatController() {
  const [chats, setChats] = useState(() => initialChats.map((c) => ({ ...c })))
  const [activeChatId, setActiveChatId] = useState(initialChats[0].id)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [effort, setEffort] = useState('Medium')
  const [status, setStatus] = useState('idle')
  const isStreaming = status === 'streaming'
  const inputRef = useRef(null)

  const activeChat = chats.find((c) => c.id === activeChatId) ?? chats[0]

  const patchMessages = useCallback((chatId, fn) => {
    setChats((all) => all.map((c) => (c.id === chatId ? { ...c, messages: fn(c.messages) } : c)))
  }, [])

  const patchAssistant = useCallback((chatId, fn) => {
    patchMessages(chatId, (messages) =>
      messages.map((m, i) =>
        i === messages.length - 1 && m.role === 'assistant' ? { ...m, ...fn(m) } : m,
      ),
    )
  }, [patchMessages])

  const sendMessage = useCallback(() => {
    const text = draft.trim()
    if (!text || status !== 'idle') return
    const chatId = activeChatId
    const userMessage = { role: 'user', content: text }
    const reasoning = EFFORTS.find((e) => e.label === effort)?.value ?? 'medium'

    patchMessages(chatId, (messages) => [...messages, userMessage])
    setDraft('')
    setStatus('streaming')

    const history = [...activeChat.messages, userMessage]
    ;(async () => {
      patchMessages(chatId, (messages) => [
        ...messages,
        { role: 'assistant', content: '', reasoning: '', tools: [], thinkingDone: false, thoughtSeconds: null },
      ])
      const streamStart = Date.now()
      let thoughtEnd = null
      const finishThinking = () => {
        if (thoughtEnd !== null) return
        thoughtEnd = Date.now()
        const seconds = (thoughtEnd - streamStart) / 1000
        patchAssistant(chatId, () => ({ thinkingDone: true, thoughtSeconds: seconds }))
      }
      await sendStreaming({ chatId, messages: history, reasoning }, {
        onToken: (word) => {
          finishThinking()
          patchAssistant(chatId, (m) => ({ content: m.content + word }))
        },
        onReasoning: (piece) => {
          patchAssistant(chatId, (m) => ({ reasoning: (m.reasoning ?? '') + piece }))
        },
        onToolCall: (call) => {
          patchAssistant(chatId, (m) => ({
            tools: [...(m.tools ?? []), {
              id: call.id,
              name: call.name,
              display: call.display || call.name,
              result: null,
              ok: null,
            }],
          }))
        },
        onToolResult: (result) => {
          patchAssistant(chatId, (m) => ({
            tools: (m.tools ?? []).map((t) =>
              t.id === result.id ? { ...t, result: result.content, ok: result.ok } : t,
            ),
          }))
        },
      })
      finishThinking()
    })().catch((err) => {
      console.error('chat error', err)
      patchMessages(chatId, (messages) =>
        messages.map((m, i) =>
          i === messages.length - 1 && m.role === 'assistant' && !m.content
            ? { ...m, content: 'Oh no, my espresso machine hiccuped. Try again in a moment! 💕' }
            : m,
        ),
      )
    }).finally(() => setStatus('idle'))
  }, [draft, status, activeChatId, activeChat, effort, patchMessages, patchAssistant])

  const newChat = useCallback(() => {
    const id = `chat-${nextId++}`
    setChats((all) => [
      {
        id,
        title: 'New chat',
        tag: null,
        messages: [],
      },
      ...all,
    ])
    setActiveChatId(id)
    setDraft('')
    setStatus('idle')
    inputRef.current?.focus()
  }, [])

  const deleteChat = useCallback((id) => {
    setChats((all) => {
      const remaining = all.filter((c) => c.id !== id)
      if (remaining.length === 0) return all
      setActiveChatId((current) => (current === id ? remaining[0].id : current))
      return remaining
    })
  }, [])

  const q = query.trim().toLowerCase()
  const visibleChats = q
    ? chats.filter((c) => c.title.toLowerCase().includes(q))
    : chats

  return {
    chats: visibleChats,
    activeChat,
    activeChatId,
    draft,
    setDraft,
    query,
    setQuery,
    effort,
    setEffort,
    isStreaming,
    inputRef,
    sendMessage,
    newChat,
    deleteChat,
    selectChat: setActiveChatId,
  }
}
