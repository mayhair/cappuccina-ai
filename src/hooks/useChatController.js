import { useCallback, useRef, useState } from 'react'
import { sendStreaming } from '../api/chatClient'
import { initialChats } from '../lib/seedChats'

let nextId = 1

export function useChatController() {
  const [chats, setChats] = useState(() => initialChats.map((c) => ({ ...c })))
  const [activeChatId, setActiveChatId] = useState(initialChats[0].id)
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('idle')
  const isTyping = status === 'typing'
  const isStreaming = status === 'streaming'
  const inputRef = useRef(null)

  const activeChat = chats.find((c) => c.id === activeChatId) ?? chats[0]

  const patchMessages = useCallback((chatId, fn) => {
    setChats((all) => all.map((c) => (c.id === chatId ? { ...c, messages: fn(c.messages) } : c)))
  }, [])

  const sendMessage = useCallback(() => {
    const text = draft.trim()
    if (!text || status !== 'idle') return
    const chatId = activeChatId
    const userMessage = { role: 'user', content: text }

    patchMessages(chatId, (messages) => [...messages, userMessage])
    setDraft('')
    // Waiting pill first — simulates the model warming up before generation.
    setStatus('typing')

    const history = [...activeChat.messages, userMessage]
    // Simulated time-to-first-token, then stream the reply through the API boundary.
    ;(async () => {
      await new Promise((r) => setTimeout(r, 900 + Math.random() * 700))
      setStatus('streaming')
      patchMessages(chatId, (messages) => [
        ...messages,
        { role: 'assistant', content: '' },
      ])
      await sendStreaming({ chatId, messages: history }, (word) => {
        patchMessages(chatId, (messages) =>
          messages.map((m, i) =>
            i === messages.length - 1 && m.role === 'assistant'
              ? { ...m, content: m.content + word }
              : m,
          ),
        )
      })
    })().catch((err) => {
      console.error('chat error', err)
      patchMessages(chatId, (messages) =>
        messages.map((m, i) =>
          i === messages.length - 1 && m.role === 'assistant' && !m.content
            ? { ...m, content: 'Ohno, my espresso machine hiccuped. Try again in a moment! 💕' }
            : m,
        ),
      )
    }).finally(() => setStatus('idle'))
  }, [draft, status, activeChatId, activeChat, patchMessages])

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
    isTyping,
    isStreaming,
    inputRef,
    sendMessage,
    newChat,
    deleteChat,
    selectChat: setActiveChatId,
  }
}
