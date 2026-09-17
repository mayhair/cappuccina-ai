import { useCallback, useEffect, useRef, useState } from 'react'
import {
  sendStreaming,
  listChats,
  createChatOnServer,
  renameChatOnServer,
  deleteChatOnServer,
} from '../api/chatClient'

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
    mode: 'chat',
    subject: null,
    messages: [],
  }
}

function normalizeChat(c) {
  return {
    ...c,
    mode: c.mode === 'tutor' ? 'tutor' : 'chat',
    subject: c.subject ?? null,
    tag: c.tag ?? (c.mode === 'tutor' ? 'TUTOR' : null),
  }
}

export function useChatController(enabled) {
  const [chats, setChats] = useState(() => [makeChat()])
  const [activeChatId, setActiveChatId] = useState(null)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [effort, setEffort] = useState(loadEffort)
  const [status, setStatus] = useState('idle')
  const [typingTitleId, setTypingTitleId] = useState(null)
  const isStreaming = status === 'streaming'
  const inputRef = useRef(null)
  const chatsRef = useRef(chats)
  const streamSeq = useRef(0)

  useEffect(() => {
    chatsRef.current = chats
  }, [chats])

  const adoptServerChat = useCallback((localId, created) => {
    setChats((all) =>
      all.map((c) =>
        c.id === localId ? normalizeChat({ ...c, id: created.id, title: created.title }) : c,
      ),
    )
    setActiveChatId((cur) => (cur === localId ? created.id : cur))
  }, [])

  // Load chat history from the server (source of truth when signed in).
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    ;(async () => {
      try {
        const stored = await listChats()
        if (cancelled) return
        if (Array.isArray(stored) && stored.length > 0) {
          setChats(stored.map(normalizeChat))
          setActiveChatId(stored[0].id)
        } else {
          // Fresh account: back the placeholder chat with a server-side one
          const localId = chatsRef.current[0].id
          const created = await createChatOnServer()
          if (cancelled) return
          adoptServerChat(localId, created)
        }
      } catch {
        // Backend unreachable — fall back to in-memory chats
        setActiveChatId((cur) => cur ?? chatsRef.current[0].id)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [enabled, adoptServerChat])

  useEffect(() => {
    try {
      localStorage.setItem(EFFORT_KEY, effort)
    } catch {
      // storage full or unavailable
    }
  }, [effort])

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

  const applyTitle = useCallback((chatId, title) => {
    const next = (title ?? '').trim()
    const target = chatsRef.current.find((c) => c.id === chatId)
    if (!next || !target || target.title !== DEFAULT_TITLE) return
    setChats((all) => all.map((c) => (c.id === chatId ? { ...c, title: next } : c)))
    setTypingTitleId(chatId)
  }, [])

  const clearTypingTitle = useCallback(() => setTypingTitleId(null), [])

  // Stamp a draft as sent in local history so re-renders (e.g. switching chats)
  // keep showing "Sent"; the server records the same on send for reloads.
  const markDraftSent = useCallback((chatId, draftId) => {
    const stamp = (list) =>
      (list ?? []).map((tool) => {
        if (!tool || tool.name !== 'draft_email' || typeof tool.result !== 'string') {
          return tool
        }
        try {
          if (JSON.parse(tool.result)?.draft_id !== draftId) return tool
        } catch {
          return tool
        }
        return { ...tool, sent: true }
      })
    setChats((all) =>
      all.map((c) =>
        c.id !== chatId
          ? c
          : {
              ...c,
              messages: c.messages.map((m) =>
                m.role !== 'assistant'
                  ? m
                  : { ...m, parts: stamp(m.parts), tools: stamp(m.tools) },
              ),
            },
      ),
    )
  }, [])

  const streamReply = useCallback((chatId, history, reasoning, mode, subject) => {
    setStatus('streaming')
    const seq = ++streamSeq.current
    ;(async () => {
      patchMessages(chatId, (messages) => [
        ...messages,
        { role: 'assistant', content: '', reasoning: '', tools: [], parts: [], thinkingDone: false, thoughtSeconds: null, failed: false },
      ])
      const streamStart = Date.now()
      let thoughtEnd = null
      const finishThinking = () => {
        if (thoughtEnd !== null) return
        thoughtEnd = Date.now()
        const seconds = (thoughtEnd - streamStart) / 1000
        patchAssistant(chatId, () => ({ thinkingDone: true, thoughtSeconds: seconds }))
      }
      await sendStreaming({
        chatId,
        messages: history,
        reasoning,
        mode,
        subject,
        generateTitle: history.length === 1,
      }, {
        onToken: (word) => {
          finishThinking()
          patchAssistant(chatId, (m) => ({ content: m.content + word }))
        },
        onReasoning: (piece) => {
          patchAssistant(chatId, (m) => {
            const parts = [...(m.parts ?? [])]
            const last = parts[parts.length - 1]
            if (last && last.type === 'reasoning') {
              parts[parts.length - 1] = { ...last, text: (last.text ?? '') + piece }
            } else {
              parts.push({ type: 'reasoning', text: piece })
            }
            return { parts, reasoning: (m.reasoning ?? '') + piece }
          })
        },
        onToolCall: (call) => {
          const tool = {
            type: 'tool',
            id: call.id,
            name: call.name,
            display: call.display || call.name,
            args: call.args ?? null,
            label: call.label ?? null,
            result: null,
            ok: null,
          }
          patchAssistant(chatId, (m) => ({
            parts: [...(m.parts ?? []), tool],
            tools: [...(m.tools ?? []), { ...tool }],
          }))
        },
        onToolResult: (result) => {
          patchAssistant(chatId, (m) => ({
            parts: (m.parts ?? []).map((p) =>
              p.type === 'tool' && p.id === result.id
                ? { ...p, result: result.content, ok: result.ok }
                : p,
            ),
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
            ? { ...m, content: 'Oh no, my espresso machine hiccuped. Try again in a moment! 💕', failed: true }
            : m,
        ),
      )
    }).finally(() => {
      if (streamSeq.current === seq) setStatus('idle')
    })
  }, [patchMessages, patchAssistant, applyTitle])

  const setMode = useCallback((chatId, mode, subject) => {
    setChats((all) =>
      all.map((c) =>
        c.id === chatId
          ? {
              ...c,
              mode,
              subject,
              tag: mode === 'tutor' ? 'TUTOR' : null,
            }
          : c,
      ),
    )
  }, [])

  const sendMessage = useCallback(() => {
    const text = draft.trim()
    if (!text || status !== 'idle') return
    const chatId = activeChat.id
    const userMessage = { role: 'user', content: text }
    const reasoning = EFFORTS.find((e) => e.label === effort)?.value ?? 'medium'

    patchMessages(chatId, (messages) => [...messages, userMessage])
    setDraft('')
    streamReply(chatId, [...activeChat.messages, userMessage], reasoning, activeChat.mode ?? 'chat', activeChat.subject ?? null)
  }, [draft, status, activeChat, effort, patchMessages, streamReply])

  const retryMessage = useCallback(() => {
    if (status !== 'idle') return
    const messages = activeChat.messages
    let lastUser = -1
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        lastUser = i
        break
      }
    }
    if (lastUser === -1) return
    const chatId = activeChat.id
    const history = messages.slice(0, lastUser + 1)
    const reasoning = EFFORTS.find((e) => e.label === effort)?.value ?? 'medium'
    patchMessages(chatId, () => history)
    streamReply(chatId, history, reasoning, activeChat.mode ?? 'chat', activeChat.subject ?? null)
  }, [status, activeChat, effort, patchMessages, streamReply])

  const newChat = useCallback(() => {
    const chat = makeChat()
    setChats((all) => [chat, ...all])
    setActiveChatId(chat.id)
    setDraft('')
    setStatus('idle')
    inputRef.current?.focus()
    createChatOnServer()
      .then((created) => adoptServerChat(chat.id, created))
      .catch(() => {
        // Creation failed — chat stays local-only and will 404 on send
      })
  }, [adoptServerChat])

  const deleteChat = useCallback((id) => {
    setChats((all) => {
      const remaining = all.filter((c) => c.id !== id)
      if (remaining.length === 0) return all
      setActiveChatId((current) => (current === id ? remaining[0].id : current))
      deleteChatOnServer(id).catch(() => {})
      return remaining
    })
  }, [])

  const renameChat = useCallback((id, title) => {
    const next = title.trim()
    if (!next) return
    setTypingTitleId(null)
    setChats((all) => all.map((c) => (c.id === id ? { ...c, title: next } : c)))
    renameChatOnServer(id, next).catch(() => {})
  }, [])

  const q = query.trim().toLowerCase()
  const visibleChats = q
    ? chats.filter((c) => c.title.toLowerCase().includes(q))
    : chats

  return {
    chats: visibleChats,
    activeChat,
    activeChatId: activeChat?.id ?? null,
    draft,
    setDraft,
    query,
    setQuery,
    effort,
    setEffort,
    setMode,
    isStreaming,
    inputRef,
    typingTitleId,
    sendMessage,
    retryMessage,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    markDraftSent,
    selectChat: setActiveChatId,
  }
}
