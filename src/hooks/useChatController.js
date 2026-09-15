import { useCallback, useEffect, useRef, useState } from 'react'
import { sendStreaming } from '../api/chatClient'

const STORAGE_KEY = 'cappuccina-chats'
const EFFORT_KEY = 'cappuccina-effort'
const DEFAULT_TITLE = 'New chat'

export const EFFORTS = [
  { label: 'Light', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
  { label: 'Extra High', value: 'xhigh' },
]

function loadEffort() {
  try {
    const saved = localStorage.getItem(EFFORT_KEY)
    if (EFFORTS.some((e) => e.label === saved)) return saved
  } catch {
    // storage unavailable
  }
  return 'Medium'
}

export function makeChat() {
  return {
    id: `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    title: DEFAULT_TITLE,
    tag: null,
    messages: [],
  }
}

function loadChats() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY))
    if (Array.isArray(stored) && stored.length > 0) return stored
  } catch {
    // ignore malformed storage
  }
  return [makeChat()]
}

export function useChatController() {
  const [chats, setChats] = useState(loadChats)
  const [activeChatId, setActiveChatId] = useState(() => chats[0].id)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [effort, setEffort] = useState(loadEffort)
  const [status, setStatus] = useState('idle')
  const [typingTitleId, setTypingTitleId] = useState(null)
  const isStreaming = status === 'streaming'
  const inputRef = useRef(null)
  const chatsRef = useRef(chats)
  const streamSeq = useRef(0)

  const activeChat = chats.find((c) => c.id === activeChatId) ?? chats[0]

  useEffect(() => {
    chatsRef.current = chats
  }, [chats])

  useEffect(() => {
    const t = setTimeout(() => {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(chats))
      } catch {
        // storage full or unavailable
      }
    }, 300)
    return () => clearTimeout(t)
  }, [chats])

  useEffect(() => {
    try {
      localStorage.setItem(EFFORT_KEY, effort)
    } catch {
      // storage full or unavailable
    }
  }, [effort])

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

  const applyTitle = useCallback((chatId, title) => {
    const next = (title ?? '').trim()
    const target = chatsRef.current.find((c) => c.id === chatId)
    if (!next || !target || target.title !== DEFAULT_TITLE) return
    setChats((all) => all.map((c) => (c.id === chatId ? { ...c, title: next } : c)))
    setTypingTitleId(chatId)
  }, [])

  const clearTypingTitle = useCallback(() => setTypingTitleId(null), [])

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
    const seq = ++streamSeq.current
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
      await sendStreaming({ chatId, messages: history, reasoning, generateTitle: history.length === 1 }, {
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
        onTitle: (title) => applyTitle(chatId, title),
        onDone: () => {
          if (streamSeq.current === seq) setStatus('idle')
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
    }).finally(() => {
      if (streamSeq.current === seq) setStatus('idle')
    })
  }, [draft, status, activeChatId, activeChat, effort, patchMessages, patchAssistant, applyTitle])

  const newChat = useCallback(() => {
    const chat = makeChat()
    setChats((all) => [chat, ...all])
    setActiveChatId(chat.id)
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

  const renameChat = useCallback((id, title) => {
    const next = title.trim()
    if (!next) return
    setTypingTitleId(null)
    setChats((all) => all.map((c) => (c.id === id ? { ...c, title: next } : c)))
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
    typingTitleId,
    sendMessage,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    selectChat: setActiveChatId,
  }
}
