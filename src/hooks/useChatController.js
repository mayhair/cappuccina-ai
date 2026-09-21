import { useCallback, useEffect, useRef, useState } from 'react'
import {
  sendStreaming,
  listChats,
  createChatOnServer,
  renameChatOnServer,
  deleteChatOnServer,
  uploadAttachment,
  deleteAttachment,
} from '../api/chatClient'

const EFFORT_KEY = 'cappuccina-effort'
const DEFAULT_TITLE = 'New chat'
export const MAX_ATTACHMENTS = 5

export const EFFORTS = [
  { label: 'Low', value: 'low' },
  { label: 'High', value: 'high' },
  { label: 'Max', value: 'max' },
]

function loadEffort() {
  try {
    const saved = localStorage.getItem(EFFORT_KEY)
    if (EFFORTS.some((e) => e.label === saved)) return saved
  } catch {
    // storage unavailable
  }
  return 'High'
}

export function makeChat() {
  return {
    id: `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    title: DEFAULT_TITLE,
    tag: null,
    mode: 'chat',
    subject: null,
    messages: [],
    // A brand-new chat lives only in memory until the first message is sent,
    // matching other AI chat apps that don't persist an empty conversation.
    pending: true,
  }
}

function normalizeChat(c) {
  return {
    ...c,
    mode: c.mode === 'tutor' ? 'tutor' : 'chat',
    subject: c.subject ?? null,
    tag: c.tag ?? (c.mode === 'tutor' ? 'TUTOR' : null),
    pending: false,
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
  // Files attached to the next message but not yet sent. They already live on
  // the server (uploaded when picked); this is just the pending tray.
  const [attachments, setAttachments] = useState([])
  const [uploading, setUploading] = useState(false)
  const [attachError, setAttachError] = useState('')
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

  // Persist a memory-only chat on first use. Returns the server id, which the
  // caller uses for streaming; the local chat is re-keyed in place.
  const persistChat = useCallback(
    async (chat) => {
      if (!chat.pending) return chat.id
      const created = await createChatOnServer(chat.mode, chat.subject)
      adoptServerChat(chat.id, created)
      return created.id
    },
    [adoptServerChat],
  )

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
          // Fresh account: keep the empty placeholder local. It is only
          // persisted once the user sends the first message.
          setActiveChatId((cur) => cur ?? chatsRef.current[0]?.id ?? null)
        }
      } catch {
        // Backend unreachable — fall back to in-memory chats
        setActiveChatId((cur) => cur ?? chatsRef.current[0].id)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [enabled])

  useEffect(() => {
    try {
      localStorage.setItem(EFFORT_KEY, effort)
    } catch {
      // storage full or unavailable
    }
  }, [effort])

  const activeChat = chats.find((c) => c.id === activeChatId) ?? chats[0]

  // Upload picked files to the server and stage them for the next message.
  // The chat is persisted first so the files can be tied to a real chat id.
  const addAttachments = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || [])
      if (!files.length || status === 'streaming') return
      const room = MAX_ATTACHMENTS - attachments.length
      if (room <= 0) {
        setAttachError(`You can attach up to ${MAX_ATTACHMENTS} files per message.`)
        return
      }
      const chosen = files.slice(0, room)
      setAttachError(
        files.length > room ? `Up to ${MAX_ATTACHMENTS} files per message.` : '',
      )
      setUploading(true)
      try {
        const chatId = await persistChat(activeChat)
        for (const file of chosen) {
          try {
            const meta = await uploadAttachment(chatId, file)
            setAttachments((list) => [...list, meta])
          } catch (err) {
            setAttachError(err.message || 'Could not attach that file.')
          }
        }
      } catch {
        setAttachError('Could not attach files — try again.')
      } finally {
        setUploading(false)
      }
    },
    [activeChat, attachments.length, persistChat, status],
  )

  const removeAttachment = useCallback((attachmentId) => {
    setAttachments((list) => list.filter((a) => a.id !== attachmentId))
    setAttachError('')
    deleteAttachment(attachmentId).catch(() => {})
  }, [])

  const clearAttachments = useCallback(() => {
    setAttachments([])
    setAttachError('')
  }, [])

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

  // Stamp a proposed calendar deletion as done in local history so re-renders
  // (e.g. switching chats) keep showing "Deleted"; the server records the same
  // on confirm for reloads.
  const markEventDeleted = useCallback((chatId, eventId) => {
    const stamp = (list) =>
      (list ?? []).map((tool) => {
        if (
          !tool ||
          tool.name !== 'propose_event_deletion' ||
          typeof tool.result !== 'string'
        ) {
          return tool
        }
        try {
          if (JSON.parse(tool.result)?.event_id !== eventId) return tool
        } catch {
          return tool
        }
        return { ...tool, deleted: true }
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
      // Per-thinking-block timing, mirroring the backend: a run of consecutive
      // reasoning/tool parts is one block; it is stamped with its duration
      // when text follows or the stream ends.
      let blockStart = null
      const openBlock = () => {
        if (blockStart === null) blockStart = Date.now()
      }
      // Ends the current thinking block and returns its duration in seconds,
      // or null when no block was open (so nothing needs stamping).
      const closeBlock = () => {
        if (blockStart === null) return null
        const seconds = (Date.now() - blockStart) / 1000
        blockStart = null
        return seconds
      }
      const stampParts = (parts, seconds) =>
        seconds == null
          ? parts
          : parts.map((p) =>
              p.type === 'text' || p.seconds != null ? p : { ...p, seconds },
            )
      await sendStreaming({
        chatId,
        messages: history,
        reasoning,
        mode,
        subject,
        generateTitle: history.length === 1,
      }, {
        onToken: (word) => {
          const seconds = closeBlock()
          patchAssistant(chatId, (m) => {
            const parts = [...(m.parts ?? [])]
            const last = parts[parts.length - 1]
            // Text becomes a part so it can sit between thinking blocks:
            // the model may say "Let me check..." then think again.
            if (last && last.type === 'text') {
              parts[parts.length - 1] = { ...last, text: (last.text ?? '') + word }
            } else {
              parts.push({ type: 'text', text: word })
            }
            return { parts: stampParts(parts, seconds), content: m.content + word }
          })
        },
        onReasoning: (piece) => {
          openBlock()
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
          openBlock()
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
        onImage: (image) => {
          // A saved server-side image; only the reference travels with the
          // history, so the next turn's full-history sync stays small.
          const part = {
            type: 'image',
            imageId: image.imageId,
            prompt: image.prompt ?? '',
            width: image.width ?? null,
            height: image.height ?? null,
          }
          patchAssistant(chatId, (m) => ({ parts: [...(m.parts ?? []), part] }))
        },
        onTitle: (title) => applyTitle(chatId, title),
        onDone: () => {
          if (streamSeq.current === seq) setStatus('idle')
        },
      })
      // Stamp the final thinking block (the stream ended while still thinking).
      const trailingSeconds = closeBlock()
      if (trailingSeconds != null) {
        patchAssistant(chatId, (m) => ({
          parts: stampParts([...(m.parts ?? [])], trailingSeconds),
        }))
      }
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
    const pending = attachments
    if ((!text && pending.length === 0) || status !== 'idle' || uploading) return
    const chat = activeChat
    const userMessage = { role: 'user', content: text }
    if (pending.length) {
      // Only ids travel with the message; bytes/text stay server-side.
      userMessage.parts = pending.map((a) => ({
        type: 'attachment',
        attachmentId: a.id,
        kind: a.kind,
        name: a.filename,
        mime: a.mime,
        size: a.size,
      }))
    }
    const reasoning = EFFORTS.find((e) => e.label === effort)?.value ?? 'high'
    const history = [...chat.messages, userMessage]

    patchMessages(chat.id, () => history)
    setDraft('')
    setAttachments([])
    setAttachError('')
    ;(async () => {
      try {
        const chatId = await persistChat(chat)
        streamReply(chatId, history, reasoning, chat.mode ?? 'chat', chat.subject ?? null)
      } catch {
        patchMessages(chat.id, (messages) => [
          ...messages,
          { role: 'assistant', content: 'Oh no, my espresso machine hiccuped. Try again in a moment! 💕', failed: true },
        ])
        setStatus('idle')
      }
    })()
  }, [draft, attachments, uploading, status, activeChat, effort, patchMessages, streamReply, persistChat])

  const retryMessage = useCallback(() => {
    if (status !== 'idle') return
    const chat = activeChat
    const messages = chat.messages
    let lastUser = -1
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') {
        lastUser = i
        break
      }
    }
    if (lastUser === -1) return
    const history = messages.slice(0, lastUser + 1)
    const reasoning = EFFORTS.find((e) => e.label === effort)?.value ?? 'high'
    patchMessages(chat.id, () => history)
    ;(async () => {
      try {
        const chatId = await persistChat(chat)
        streamReply(chatId, history, reasoning, chat.mode ?? 'chat', chat.subject ?? null)
      } catch {
        patchMessages(chat.id, (msgs) => [
          ...msgs,
          { role: 'assistant', content: 'Oh no, my espresso machine hiccuped. Try again in a moment! 💕', failed: true },
        ])
        setStatus('idle')
      }
    })()
  }, [status, activeChat, effort, patchMessages, streamReply, persistChat])

  const newChat = useCallback(() => {
    const chat = makeChat()
    // Only one pristine new chat can exist; drop any other empty ones.
    setChats((all) => [chat, ...all.filter((c) => !c.pending || c.messages.length > 0)])
    setActiveChatId(chat.id)
    setDraft('')
    setStatus('idle')
    setAttachments([])
    setAttachError('')
    inputRef.current?.focus()
  }, [])

  const deleteChat = useCallback((id) => {
    setChats((all) => {
      const target = all.find((c) => c.id === id)
      const remaining = all.filter((c) => c.id !== id)
      if (remaining.length === 0) return all
      setActiveChatId((current) => (current === id ? remaining[0].id : current))
      if (!target?.pending) deleteChatOnServer(id).catch(() => {})
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

  // Pending attachments belong to one chat; switching clears the tray so they
  // never follow the user into a different conversation.
  const selectChat = useCallback((id) => {
    setActiveChatId(id)
    setAttachments([])
    setAttachError('')
  }, [])

  const q = query.trim().toLowerCase()
  // Empty not-yet-persisted chats stay out of the sidebar until their first message.
  const listed = chats.filter((c) => !c.pending || c.messages.length > 0)
  const visibleChats = q
    ? listed.filter((c) => c.title.toLowerCase().includes(q))
    : listed

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
    markEventDeleted,
    selectChat,
    attachments,
    uploading,
    attachError,
    addAttachments,
    removeAttachment,
    clearAttachments,
  }
}
