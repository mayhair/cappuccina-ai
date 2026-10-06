import { useCallback, useEffect, useRef, useState } from 'react'
import {
  sendStreaming,
  listChats,
  createChatOnServer,
  renameChatOnServer,
  deleteChatOnServer,
  setActiveBranchOnServer,
  uploadAttachment,
  deleteAttachment,
} from '../api/chatClient'
import { getWebpageEnv, getWebpageVersions, activateWebpageVersion, saveWebpageFile } from '../api/webpageClient'
import tts from '../lib/tts'
import {
  makeMessageId,
  nextBranchIndex,
  visibleMessages,
  navigate as navigateBranchPath,
  defaultPath,
} from '../lib/branching'

const EFFORT_KEY = 'cappuccina-effort'
const ACTIVE_CHAT_KEY = 'cappuccina-active-chat'
const DEFAULT_TITLE = 'New chat'
const STREAM_FAILED_MESSAGE = 'Oh no, my espresso machine hiccuped. Try again in a moment! 💕'
export const MAX_ATTACHMENTS = 5

// The canvas state of a chat with no webpage (yet): the same shape the server
// sends, so the canvas can render it before the first fetch lands.
const EMPTY_WEBPAGE = {
  webpageId: null,
  files: [],
  previewUrl: null,
  previewBase: null,
  activeVersionId: null,
  versions: [],
  hasBuild: false,
  lastBuild: null,
  updatedAt: null,
}

export const EFFORTS = [
  { label: 'Low', value: 'low' },
  { label: 'High', value: 'high' },
  { label: 'Max', value: 'max' },
]

// Wire value for the API (`reasoning` field) from the stored display label.
function effortValue(effort) {
  return EFFORTS.find((e) => e.label === effort)?.value ?? 'high'
}

function loadEffort() {
  try {
    const saved = localStorage.getItem(EFFORT_KEY)
    if (EFFORTS.some((e) => e.label === saved)) return saved
  } catch {
    // storage unavailable
  }
  return 'High'
}

function loadActiveChatId() {
  try {
    return localStorage.getItem(ACTIVE_CHAT_KEY)
  } catch {
    // storage unavailable
  }
  return null
}

export function makeChat() {
  return {
    id: `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    title: DEFAULT_TITLE,
    tag: null,
    mode: 'chat',
    subject: null,
    // Every message node (all branches); activePath is the visible chain.
    messages: [],
    activePath: [],
    // Tail of the branch being viewed. Persisted server-side (chat
    // active_leaf_uid) so the same branch restores across reloads/devices.
    activeLeafId: null,
    // A brand-new chat lives only in memory until the first message is sent,
    // matching other AI chat apps that don't persist an empty conversation.
    pending: true,
  }
}

// Rebuild the visible chain from the persisted branch tail: walk parent links
// back to the root, then reserve. Falls back to the newest branch when the
// saved tail is gone.
function pathFromActiveLeaf(messages, leafId) {
  const byId = new Map(messages.map((m) => [m.id, m]))
  const leaf = leafId ? byId.get(leafId) : null
  if (!leaf) return defaultPath(messages)
  const chain = []
  let cur = leaf
  const seen = new Set()
  while (cur && !seen.has(cur.id)) {
    seen.add(cur.id)
    chain.push(cur.id)
    cur = cur.parentId ? byId.get(cur.parentId) : null
  }
  return chain.reverse()
}

function normalizeChat(c) {
  const messages = (c.messages ?? []).map((m) => ({
    ...m,
    id: m.id ?? makeMessageId(),
    parentId: m.parentId ?? null,
    branchIndex: m.branchIndex ?? 0,
  }))
  const activeLeafId = c.activeLeafId ?? c.active_leaf_uid ?? null
  return {
    ...c,
    messages,
    activeLeafId,
    activePath: pathFromActiveLeaf(messages, activeLeafId),
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
  // The chat's webpage environment, keyed by chat id: {webpageId, files
  // (names), previewUrl, previewBase, activeVersionId, versions, hasBuild,
  // lastBuild, updatedAt}. Only names and metadata live here — a file's text is
  // fetched on demand and never held — and the server is the source of truth,
  // so all of it is refetched when the user switches chats.
  // `canvasOpen` is the canvas panel's visibility, `canvasNotice` a one-line
  // failure message shown in the canvas, and `canvasRevision` counts the
  // changes that must reload the preview iframe (a new iteration, an
  // activation, a save).
  const [webpages, setWebpages] = useState({})
  const [canvasOpen, setCanvasOpen] = useState(false)
  const [canvasNotice, setCanvasNotice] = useState(null)
  const [canvasBusy, setCanvasBusy] = useState(false)
  const [canvasRevision, setCanvasRevision] = useState(0)
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
          const restored = stored.map(normalizeChat)
          setChats(restored)
          const saved = loadActiveChatId()
          // Fall back to the newest chat when the saved one is gone.
          setActiveChatId(restored.some((c) => c.id === saved) ? saved : stored[0].id)
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

  // Remember the selected chat so a reload lands on the same conversation.
  // Pending (memory-only) chats have no server id and are skipped.
  useEffect(() => {
    if (!activeChatId) return
    const chat = chatsRef.current.find((c) => c.id === activeChatId)
    if (!chat || chat.pending) return
    try {
      localStorage.setItem(ACTIVE_CHAT_KEY, activeChatId)
    } catch {
      // storage full or unavailable
    }
  }, [activeChatId])

  const activeChat = chats.find((c) => c.id === activeChatId) ?? chats[0]

  // Fold one env response (fetch, activation or save) into the chat's canvas
  // state: all three return the same shape and all three replace the working
  // copy, the preview URL and the active iteration from it.
  const applyWebpageEnv = useCallback((chatId, data) => {
    setWebpages((prev) => ({
      ...prev,
      [chatId]: {
        ...(prev[chatId] ?? {}),
        webpageId: data.webpageId ?? null,
        files: data.files ?? [],
        previewUrl: data.previewUrl ?? null,
        previewBase: data.previewBase ?? null,
        hasBuild: Boolean(data.hasBuild),
        lastBuild: data.lastBuild ?? null,
        updatedAt: data.updatedAt ?? prev[chatId]?.updatedAt ?? null,
        activeVersionId: data.activeVersionId ?? null,
      },
    }))
  }, [])

  // Load the active chat's environment and iteration list from the server
  // (source of truth) so the canvas is correct after a reload or a switch from
  // another device. Unconditional: a chat without a page answers with a null
  // webpageId, and one small fetch each is cheaper than guessing.
  useEffect(() => {
    if (!enabled || !activeChatId) return
    const chat = chatsRef.current.find((c) => c.id === activeChatId)
    if (!chat || chat.pending) return
    let cancelled = false
    Promise.all([getWebpageEnv(activeChatId), getWebpageVersions(activeChatId)])
      .then(([env, list]) => {
        if (cancelled) return
        applyWebpageEnv(activeChatId, env)
        setWebpages((prev) => ({
          ...prev,
          [activeChatId]: {
            ...(prev[activeChatId] ?? EMPTY_WEBPAGE),
            activeVersionId: env.activeVersionId ?? list.activeVersionId ?? null,
            versions: list.versions ?? [],
          },
        }))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [activeChatId, enabled, applyWebpageEnv])

  // Iterations are refetched whenever a run writes a page and when the canvas
  // opens: a commit appears mid-stream, and pruning can drop old rows.
  const refreshWebpageVersions = useCallback(async (chatId) => {
    try {
      const data = await getWebpageVersions(chatId)
      setWebpages((prev) => ({
        ...prev,
        [chatId]: {
          ...(prev[chatId] ?? EMPTY_WEBPAGE),
          activeVersionId: data.activeVersionId ?? null,
          versions: data.versions ?? [],
        },
      }))
    } catch {
      // The canvas falls back to hiding the history bar.
    }
  }, [])

  // Fetch the environment again (a run wrote files, or a mutation was
  // rejected and the stale local state must give way): the server's copy wins.
  const refreshWebpageEnv = useCallback(async (chatId) => {
    try {
      const env = await getWebpageEnv(chatId)
      applyWebpageEnv(chatId, env)
    } catch {
      // The canvas keeps showing what it has.
    }
  }, [applyWebpageEnv])

  // Clicking an iteration activates it immediately: the canvas always renders
  // the active one, so there is no separate browse state.
  const activateVersion = useCallback(
    async (chatId, versionId) => {
      setCanvasBusy(true)
      try {
        const data = await activateWebpageVersion(chatId, versionId)
        applyWebpageEnv(chatId, data)
        // The working copy moved back to that iteration: reload the iframe.
        setCanvasRevision((r) => r + 1)
        setCanvasNotice(null)
      } catch (err) {
        setCanvasNotice(err.status === 409 ? err.message : 'Could not switch iteration.')
        refreshWebpageEnv(chatId)
        refreshWebpageVersions(chatId)
      } finally {
        setCanvasBusy(false)
      }
    },
    [applyWebpageEnv, refreshWebpageEnv, refreshWebpageVersions],
  )

  // A manual edit lands as its own iteration. Returns false when the save was
  // refused (stale tab or the reply was still streaming) so the canvas keeps
  // the user's draft text.
  const saveFile = useCallback(
    async (chatId, path, content, baseVersionId) => {
      setCanvasBusy(true)
      try {
        const data = await saveWebpageFile(chatId, { path, content, baseVersionId })
        applyWebpageEnv(chatId, data)
        // The file landed in the workspace (and a rebuild may have run): the
        // served page changed, so reload the iframe and re-read the file list.
        setCanvasRevision((r) => r + 1)
        refreshWebpageVersions(chatId)
        setCanvasNotice(null)
        return true
      } catch (err) {
        setCanvasNotice(err.status === 409 ? err.message : 'Could not save that file.')
        refreshWebpageEnv(chatId)
        refreshWebpageVersions(chatId)
        return false
      } finally {
        setCanvasBusy(false)
      }
    },
    [applyWebpageEnv, refreshWebpageEnv, refreshWebpageVersions],
  )

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

  // Patch a single message node by id (used while streaming the reply).
  const patchNode = useCallback((chatId, nodeId, fn) => {
    patchMessages(chatId, (messages) =>
      messages.map((m) => (m.id === nodeId ? { ...m, ...fn(m) } : m)),
    )
  }, [patchMessages])

  const patchChatState = useCallback((chatId, fn) => {
    setChats((all) => all.map((c) => (c.id === chatId ? fn(c) : c)))
  }, [])

  // Remember the viewed branch tail both locally and on the server so it
  // survives reloads and follows the user to other devices.
  const setActiveLeaf = useCallback((chatId, leafId) => {
    patchChatState(chatId, (c) => ({ ...c, activeLeafId: leafId }))
    const chat = chatsRef.current.find((c) => c.id === chatId)
    if (chat && !chat.pending) {
      setActiveBranchOnServer(chatId, leafId).catch(() => {})
    }
  }, [patchChatState])

  const applyTitle = useCallback((chatId, title) => {
    const next = (title ?? '').trim()
    const target = chatsRef.current.find((c) => c.id === chatId)
    if (!next || !target || target.title !== DEFAULT_TITLE) return
    setChats((all) => all.map((c) => (c.id === chatId ? { ...c, title: next } : c)))
    setTypingTitleId(chatId)
  }, [])

  const clearTypingTitle = useCallback(() => setTypingTitleId(null), [])

  // Stamp a tool result as done in local history so re-renders (e.g. switching
  // chats) keep showing the completed state; the server records the same on the
  // confirming action for reloads.
  const markToolFlagged = useCallback((chatId, toolName, idKey, idValue, flag) => {
    const stamp = (list) =>
      (list ?? []).map((tool) => {
        if (!tool || tool.name !== toolName || typeof tool.result !== 'string') {
          return tool
        }
        try {
          if (JSON.parse(tool.result)?.[idKey] !== idValue) return tool
        } catch {
          return tool
        }
        return { ...tool, [flag]: true }
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

  const markDraftSent = useCallback(
    (chatId, draftId) => markToolFlagged(chatId, 'draft_email', 'draft_id', draftId, 'sent'),
    [markToolFlagged],
  )

  const markEventDeleted = useCallback(
    (chatId, eventId) =>
      markToolFlagged(chatId, 'propose_event_deletion', 'event_id', eventId, 'deleted'),
    [markToolFlagged],
  )

  // Persist the chat if needed, then stream the model's reply. `pathMessages`
  // is the visible chain the model should see (ending at the message being
  // replied to); the streamed reply node is parented to its last entry.
  const runStream = useCallback((chat, pathMessages) => {
    const reasoning = effortValue(effort)
    tts.stop()
    // Live nodes already known to the tree plus any fresh nodes (a new edit or
    // regenerate) not stored yet, so branch order matches the server's.
    const base = chat.messages.filter((m) => !m.failed)
    const known = new Set(base.map((m) => m.id))
    const fresh = pathMessages.filter((m) => !known.has(m.id))
    const treeNodes = [...base, ...fresh]
    const parent = pathMessages[pathMessages.length - 1] ?? null
    const parentId = parent?.id ?? null
    const replyId = makeMessageId()
    const placeholder = {
      id: replyId,
      parentId,
      branchIndex: nextBranchIndex(treeNodes, parentId),
      role: 'assistant',
      content: '',
      reasoning: '',
      tools: [],
      parts: [],
      thinkingDone: false,
      thoughtSeconds: null,
      failed: false,
    }
    setStatus('streaming')
    const seq = ++streamSeq.current
    ;(async () => {
      let chatId
      try {
        chatId = await persistChat(chat)
      } catch (err) {
        console.error('chat error', err)
        const failedId = makeMessageId()
        patchChatState(chat.id, (c) => ({
          ...c,
          messages: [
            ...c.messages.filter((m) => !m.failed),
            ...fresh,
            {
              id: failedId,
              parentId,
              branchIndex: nextBranchIndex(treeNodes, parentId),
              role: 'assistant',
              content: STREAM_FAILED_MESSAGE,
              failed: true,
            },
          ],
          activePath: [...pathMessages.map((m) => m.id), failedId],
        }))
        setStatus('idle')
        return
      }
      patchChatState(chatId, (c) => ({
        ...c,
        messages: [...c.messages.filter((m) => !m.failed), ...fresh, placeholder],
        activePath: [...pathMessages.map((m) => m.id), replyId],
        activeLeafId: replyId,
      }))
      // Per-thinking-block timing, mirroring the backend: a run of consecutive
      // reasoning/tool parts is one block; it is stamped with its duration
      // when text follows or the stream ends.
      let blockStart = null
      // Text streamed for the bubble currently open. Each bubble is read
      // aloud the moment it finishes — when thinking, a tool call or an image
      // follows, or the stream ends — instead of waiting for the whole run.
      let spokenText = ''
      const flushSpoken = () => {
        const text = spokenText
        spokenText = ''
        if (!text.trim() || streamSeq.current !== seq) return
        // Only the streamed text is spoken (never reasoning/tool parts).
        tts.enqueue({ parts: [{ type: 'text', text }] })
      }
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
        messages: pathMessages,
        tree: treeNodes,
        replyId,
        reasoning,
        mode: chat.mode ?? 'chat',
        subject: chat.subject ?? null,
        generateTitle: pathMessages.length === 1,
      }, {
        onToken: (word) => {
          if (!word) return
          spokenText += word
          const seconds = closeBlock()
          patchNode(chatId, replyId, (m) => {
            const parts = [...(m.parts ?? [])]
            const last = parts[parts.length - 1]
            // Text becomes a part so it can sit between thinking blocks:
            // the model may say "Let me check..." then think again. A
            // whitespace-only chunk with no open text part is skipped — it
            // would otherwise render as an empty bubble.
            if (last && last.type === 'text') {
              parts[parts.length - 1] = { ...last, text: (last.text ?? '') + word }
            } else if (word.trim()) {
              parts.push({ type: 'text', text: word })
            }
            return { parts: stampParts(parts, seconds), content: (m.content ?? '') + word }
          })
        },
        onReasoning: (piece) => {
          flushSpoken()
          openBlock()
          patchNode(chatId, replyId, (m) => {
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
          flushSpoken()
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
          // A tool call is announced twice: once while the model streams its
          // name (so the card shows during a long argument, like a webpage
          // write) and again when it starts running with the real args. Upsert
          // by id so both land on one card.
          patchNode(chatId, replyId, (m) => {
            const update = { name: tool.name, display: tool.display, args: tool.args, label: tool.label }
            const parts = [...(m.parts ?? [])]
            const at = parts.findIndex((p) => p.type === 'tool' && p.id === call.id)
            if (at === -1) parts.push(tool)
            else parts[at] = { ...parts[at], ...update }
            const tools = [...(m.tools ?? [])]
            const toolAt = tools.findIndex((t) => t.id === call.id)
            if (toolAt === -1) tools.push({ ...tool })
            else tools[toolAt] = { ...tools[toolAt], ...update }
            return { parts, tools }
          })
        },
        onToolResult: (result) => {
          patchNode(chatId, replyId, (m) => ({
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
          flushSpoken()
          // A saved server-side image; only the reference travels with the
          // history, so the next turn's full-history sync stays small.
          const part = {
            type: 'image',
            imageId: image.imageId,
            prompt: image.prompt ?? '',
            width: image.width ?? null,
            height: image.height ?? null,
          }
          if (image.transparent === true) part.transparent = true
          patchNode(chatId, replyId, (m) => ({ parts: [...(m.parts ?? []), part] }))
        },
        onWebpage: (webpage) => {
          flushSpoken()
          // The write is recorded as a small server-side reference; the file
          // text stays in the workspace and is fetched per file. A page is
          // several writes: fold them into one part listing the files.
          const part = {
            type: 'webpage',
            webpageId: webpage.webpageId,
            files: webpage.files ?? [],
          }
          if (webpage.path) part.path = webpage.path
          patchNode(chatId, replyId, (m) => {
            const parts = [...(m.parts ?? [])]
            const at = parts.findIndex(
              (p) => p.type === 'webpage' && p.webpageId === part.webpageId,
            )
            if (at === -1) parts.push(part)
            else parts[at] = { ...parts[at], ...part }
            return { parts }
          })
          // The environment, the iteration list and the build state all live
          // server-side: refetch them, but only while this stream is still the
          // live one — a newer stream's write must not resurrect older state.
          if (streamSeq.current === seq) {
            refreshWebpageEnv(chatId)
            refreshWebpageVersions(chatId)
          }
          // The page changed under the iframe: load the served version once
          // more. A page is appearing: show the canvas without making the user
          // ask.
          setCanvasRevision((r) => r + 1)
          setCanvasOpen(true)
        },
        onTitle: (title) => applyTitle(chatId, title),
        onDone: () => {
          if (streamSeq.current === seq) setStatus('idle')
        },
      })
      // The stream ended: any trailing bubble ends with it and is read now.
      flushSpoken()
      // Stamp the final thinking block (the stream ended while still thinking).
      const trailingSeconds = closeBlock()
      if (trailingSeconds != null) {
        patchNode(chatId, replyId, (m) => ({
          parts: stampParts([...(m.parts ?? [])], trailingSeconds),
        }))
      }
    })().catch((err) => {
      console.error('chat error', err)
      patchMessages(chatId, (messages) =>
        messages.map((m) =>
          m.id === replyId && !m.content
            ? { ...m, content: STREAM_FAILED_MESSAGE, failed: true }
            : m,
        ),
      )
    }).finally(() => {
      if (streamSeq.current === seq) setStatus('idle')
    })
  }, [effort, patchMessages, patchNode, patchChatState, persistChat, applyTitle, refreshWebpageEnv, refreshWebpageVersions])

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
    // Failed stream tails are transient: a new turn follows the last real node.
    const visible = visibleMessages(chat).filter((m) => !m.failed)
    const parentId = visible.length ? visible[visible.length - 1].id : null
    const userMessage = {
      id: makeMessageId(),
      parentId,
      branchIndex: nextBranchIndex(chat.messages, parentId),
      role: 'user',
      content: text,
    }
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

    setDraft('')
    clearAttachments()
    runStream(chat, [...visible, userMessage])
  }, [draft, attachments, uploading, status, activeChat, clearAttachments, runStream])

  // Edit a sent user message: the new text becomes a sibling version and a
  // fresh reply is streamed under it. The old branch stays navigable.
  const editMessage = useCallback((messageId, text) => {
    const next = (text ?? '').trim()
    if (!next || status !== 'idle') return
    const chat = activeChat
    const visible = visibleMessages(chat)
    const at = visible.findIndex((m) => m.id === messageId)
    if (at === -1) return
    const original = visible[at]
    const parentId = original.parentId ?? null
    runStream(chat, [
      ...visible.slice(0, at),
      {
        ...original,
        id: makeMessageId(),
        content: next,
        parentId,
        branchIndex: nextBranchIndex(chat.messages, parentId),
        failed: false,
      },
    ])
  }, [status, activeChat, runStream])

  // Regenerate an assistant reply: stream a new sibling version from the same
  // prompt, keeping the previous answer reachable with the arrows.
  const regenerateMessage = useCallback((messageId) => {
    if (status !== 'idle') return
    const chat = activeChat
    const visible = visibleMessages(chat)
    const at = visible.findIndex((m) => m.id === messageId)
    if (at <= 0) return
    runStream(chat, visible.slice(0, at))
  }, [status, activeChat, runStream])

  const retryMessage = useCallback(() => {
    if (status !== 'idle') return
    const chat = activeChat
    const visible = visibleMessages(chat)
    let lastUser = -1
    for (let i = visible.length - 1; i >= 0; i--) {
      if (visible[i].role === 'user') {
        lastUser = i
        break
      }
    }
    if (lastUser === -1) return
    runStream(chat, visible.slice(0, lastUser + 1))
  }, [status, activeChat, runStream])

  // Move up/down the sibling versions of a message; the visible tail follows
  // the chosen version's newest branch.
  const navigateBranch = useCallback((messageId, delta) => {
    if (status !== 'idle') return
    const chat = activeChat
    const next = navigateBranchPath(chat.messages, chat.activePath ?? [], messageId, delta)
    tts.stop()
    patchChatState(chat.id, (c) => ({ ...c, activePath: next }))
    // Persist the new tail (last node on the visible chain).
    setActiveLeaf(chat.id, next[next.length - 1] ?? null)
  }, [status, activeChat, patchChatState, setActiveLeaf])

  const newChat = useCallback(() => {
    const chat = makeChat()
    // Only one pristine new chat can exist; drop any other empty ones.
    setChats((all) => [chat, ...all.filter((c) => !c.pending || c.messages.length > 0)])
    setActiveChatId(chat.id)
    tts.stop()
    setDraft('')
    setStatus('idle')
    clearAttachments()
    inputRef.current?.focus()
  }, [])

  const deleteChat = useCallback((id) => {
    const all = chatsRef.current
    const target = all.find((c) => c.id === id)
    const remaining = all.filter((c) => c.id !== id)
    if (remaining.length === 0) return
    // React can run state updaters twice (StrictMode), so the server DELETE and
    // the active-chat switch live here rather than inside the setChats updater.
    setActiveChatId((current) => (current === id ? remaining[0].id : current))
    if (!target?.pending) deleteChatOnServer(id).catch(() => {})
    setChats(remaining)
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
    tts.stop()
    clearAttachments()
  }, [])

  // Close the canvas when the user leaves a chat that had one open.
  const selectChatAndCanvas = useCallback((id) => {
    setCanvasOpen(false)
    setCanvasNotice(null)
    selectChat(id)
  }, [selectChat])

  // Closing hides the panel but keeps the page: the top-bar toggle and the
  // message card can reopen it at any time (and a later turn edits it).
  const closeWebpage = useCallback(() => setCanvasOpen(false), [])

  const clearCanvasNotice = useCallback(() => setCanvasNotice(null), [])

  const openCanvas = useCallback(() => setCanvasOpen(true), [])
  const toggleCanvas = useCallback(() => setCanvasOpen((open) => !open), [])

  const activeWebpage = activeChat ? webpages[activeChat.id] ?? EMPTY_WEBPAGE : EMPTY_WEBPAGE
  // The toggle matches the message card: a turn that built a page shows both,
  // even before the env fetch lands (or if it can't). A chat whose parts
  // predate the workspace rewrite has no site id, so the part is the fallback.
  const hasWebpage =
    activeWebpage.webpageId != null ||
    Boolean(activeChat?.messages?.some((m) => (m.parts ?? []).some((p) => p.type === 'webpage')))

  const q = query.trim().toLowerCase()
  // Empty not-yet-persisted chats stay out of the sidebar until their first message.
  const listed = chats.filter((c) => !c.pending || c.messages.length > 0)
  const visibleChats = q
    ? listed.filter((c) => c.title.toLowerCase().includes(q))
    : listed

  // The rendered chat shows only the active branch (visible chain); the full
  // tree travels as `allMessages` so the UI can count sibling versions.
  const activeView = activeChat
    ? { ...activeChat, messages: visibleMessages(activeChat), allMessages: activeChat.messages }
    : activeChat

  return {
    chats: visibleChats,
    activeChat: activeView,
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
    editMessage,
    regenerateMessage,
    navigateBranch,
    newChat,
    deleteChat,
    renameChat,
    clearTypingTitle,
    markDraftSent,
    markEventDeleted,
    selectChat: selectChatAndCanvas,
    attachments,
    uploading,
    attachError,
    addAttachments,
    removeAttachment,
    webpage: { ...activeWebpage, chatId: activeChat?.id ?? null },
    hasWebpage,
    canvasRevision,
    canvasOpen,
    canvasNotice,
    clearCanvasNotice,
    canvasBusy,
    activateVersion,
    saveFile,
    openCanvas,
    closeCanvas: closeWebpage,
    toggleCanvas,
  }
}
