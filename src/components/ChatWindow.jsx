import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { SendIcon, ChevronIcon, WrenchIcon, CopyIcon, CheckIcon, RetryIcon, MailIcon, PencilIcon } from './Icons'
import { EffortSelector } from './EffortSelector'
import { ModeSwitch } from './ModeSwitch'
import { Markdown } from '../lib/markdown'
import { EmojiText } from '../lib/emoji'
import { sendDraft, updateDraft } from '../api/gmailClient'

const isTouchKeyboard = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(hover: none) and (pointer: coarse)').matches

export function StreamingDots({ label = 'Cappuccina is typing' }) {
  return (
    <span className="streaming" role="status" aria-label={label}>
      <i /><i /><i />
    </span>
  )
}

function formatThought(seconds) {
  const s = Math.round(seconds * 10) / 10
  const shown = Number.isInteger(s) ? String(s) : s.toFixed(1)
  return `Thought for ${shown} second${s === 1 ? '' : 's'}`
}

function thinkingParts(message) {
  // Ordered interleaving of reasoning + tool calls + text. Falls back to the
  // legacy grouped shape (reasoning blob, then tools) for messages stored
  // before parts existed.
  if (Array.isArray(message.parts) && message.parts.length > 0) return message.parts
  const out = []
  const reasoning = message.reasoning ?? ''
  const tools = message.tools ?? []
  if (reasoning) out.push({ type: 'reasoning', text: reasoning })
  for (const t of tools) out.push({ type: 'tool', ...t })
  return out
}

// A model may interleave text with thinking ("Let me search for that" then
// more reasoning then the answer). Group consecutive non-text parts into a
// thinking segment and emit each text part as its own segment, so the UI can
// render a new thinking block immediately after a chat message.
function messageSegments(message) {
  const raw = thinkingParts(message)
  const hasTextPart = raw.some((p) => p.type === 'text')
  const parts = hasTextPart
    ? raw
    : message.content
      ? [...raw, { type: 'text', text: message.content }]
      : raw
  const segments = []
  let thinking = []
  const flushThinking = () => {
    if (thinking.length) {
      segments.push({ type: 'thinking', parts: thinking })
      thinking = []
    }
  }
  for (const part of parts) {
    if (part.type === 'text') {
      flushThinking()
      segments.push({ type: 'text', text: part.text ?? '' })
    } else {
      thinking.push(part)
    }
  }
  flushThinking()
  return segments
}

function formatArgs(args) {
  if (args == null) return null
  if (typeof args === 'string') return args
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}

// A successful draft_email tool result carries JSON the UI turns into a
// reviewable card with an explicit Send button.
function draftFromTool(tool) {
  if (tool?.name !== 'draft_email' || typeof tool.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    if (!(data?.status === 'draft_created' && data.draft_id)) return null
    // `sent` is stamped by the server on reload and by the client after sending.
    return { ...data, sent: tool.sent === true || data.sent === true }
  } catch {
    return null
  }
}

// A generate_image result is a machine-readable pointer at the saved picture
// (see backend/app/main.py); the picture itself renders as an image card
// below the message, so the thinking panel only needs a one-liner.
function imageFromPart(part) {
  if (part?.type !== 'image' || typeof part.imageId !== 'string' || !part.imageId) {
    return null
  }
  return part
}

function imageResultSummary(tool) {
  if (tool?.name !== 'generate_image' || typeof tool.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    return data?.status === 'image_generated' && data.image_id
      ? 'Picture ready — see below ✨'
      : null
  } catch {
    return null
  }
}

function ImageCard({ image }) {
  const [open, setOpen] = useState(false)
  const src = `/api/v1/images/${image.imageId}`
  const alt = image.prompt ? `Picture Cappuccina drew: ${image.prompt}` : 'Picture Cappuccina drew'

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
    }
  }, [open])

  return (
    <figure className="image-card">
      <button
        type="button"
        className="image-card-frame"
        onClick={() => setOpen(true)}
        aria-label="View picture full size"
      >
        <img
          src={src}
          alt={alt}
          loading="lazy"
          width={image.width ?? undefined}
          height={image.height ?? undefined}
        />
      </button>
      {image.prompt && <figcaption className="image-card-caption">{image.prompt}</figcaption>}
      {open &&
        createPortal(
          <div
            className="image-lightbox"
            role="dialog"
            aria-modal="true"
            aria-label={alt}
            onClick={(e) => {
              if (e.target === e.currentTarget) setOpen(false)
            }}
          >
            <img src={src} alt={alt} onClick={(e) => e.stopPropagation()} />
            <button
              type="button"
              className="image-lightbox-close"
              onClick={() => setOpen(false)}
              aria-label="Close full-size picture"
            >
              ×
            </button>
          </div>,
          document.body,
        )}
    </figure>
  )
}

function DraftCard({ draft, onSent }) {
  const initial = { to: draft.to, subject: draft.subject, body: draft.body }
  const [saved, setSaved] = useState(initial) // last persisted contents
  const [fields, setFields] = useState(initial)
  const [editing, setEditing] = useState(false)
  // A previously sent draft (from a reload) starts in the sent state.
  const [state, setState] = useState(draft.sent ? 'sent' : 'idle')
  const [message, setMessage] = useState('')

  const dirty =
    fields.to !== saved.to ||
    fields.subject !== saved.subject ||
    fields.body !== saved.body
  const canSubmit = fields.to.trim().length > 0

  const startEditing = () => {
    setEditing(true)
    setState('idle')
    setMessage('')
  }

  const handleError = (err) => {
    if (err.status === 409 && /already (?:been |have been )?sent|no longer exists/i.test(err.message)) {
      setState('sent')
      onSent?.(draft.draft_id)
      return
    }
    setState('error')
    setMessage(err.message || 'Something went wrong — try again')
  }

  const save = async () => {
    if (!canSubmit || !dirty) return
    setState('saving')
    setMessage('')
    try {
      await updateDraft(draft.draft_id, fields)
      setSaved(fields)
      setEditing(false)
      setState('idle')
    } catch (err) {
      handleError(err)
    }
  }

  const send = async () => {
    if (!canSubmit || state === 'sending' || state === 'sent') return
    setState('sending')
    setMessage('')
    try {
      // Picking up un-saved edits here means Send always sends what's on screen.
      if (dirty) {
        await updateDraft(draft.draft_id, fields)
        setSaved(fields)
      }
      await sendDraft(draft.draft_id)
      setState('sent')
      onSent?.(draft.draft_id)
    } catch (err) {
      handleError(err)
    }
  }

  const cancel = () => {
    setFields(saved)
    setEditing(false)
    setState('idle')
    setMessage('')
  }

  return (
    <div className="draft-card">
      <div className="draft-card-head">
        <MailIcon />
        Email draft
        {!editing && state !== 'sent' && (
          <button type="button" className="draft-edit" onClick={startEditing}>
            <PencilIcon /> Edit
          </button>
        )}
      </div>

      {editing ? (
        <div className="draft-edit-form">
          <label className="draft-edit-field">
            <span>To</span>
            <input
              type="email"
              value={fields.to}
              onChange={(e) => setFields({ ...fields, to: e.target.value })}
              aria-label="Recipient"
            />
          </label>
          <label className="draft-edit-field">
            <span>Subject</span>
            <input
              type="text"
              value={fields.subject}
              onChange={(e) => setFields({ ...fields, subject: e.target.value })}
              aria-label="Subject"
            />
          </label>
          <label className="draft-edit-field">
            <span>Message</span>
            <textarea
              rows={6}
              value={fields.body}
              onChange={(e) => setFields({ ...fields, body: e.target.value })}
              aria-label="Message body"
            />
          </label>
        </div>
      ) : (
        <>
          <div className="draft-fields">
            <div><b>To</b>{fields.to}</div>
            <div><b>Subject</b>{fields.subject}</div>
          </div>
          <p
            className="draft-body"
            role={state === 'sent' ? undefined : 'button'}
            tabIndex={state === 'sent' ? undefined : 0}
            title={state === 'sent' ? undefined : 'Click to edit'}
            onClick={state === 'sent' ? undefined : startEditing}
            onKeyDown={
              state === 'sent'
                ? undefined
                : (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      startEditing()
                    }
                  }
            }
          >
            {fields.body}
          </p>
        </>
      )}

      {state === 'error' && (
        <p className="draft-note error draft-error">{message}</p>
      )}

      <div className="draft-actions">
        {state === 'sent' ? (
          <span className="draft-note ok"><CheckIcon /> Sent</span>
        ) : editing ? (
          <>
            <button
              type="button"
              className="draft-send"
              onClick={save}
              disabled={state === 'saving' || !canSubmit || !dirty}
            >
              {state === 'saving' ? 'Saving…' : 'Save changes'}
            </button>
            <button
              type="button"
              className="draft-secondary"
              onClick={cancel}
              disabled={state === 'saving'}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="draft-send"
              onClick={send}
              disabled={state === 'sending' || !canSubmit}
            >
              {state === 'sending' ? 'Sending…' : 'Send email'}
            </button>
            <span className="draft-note">Click the message to edit it</span>
          </>
        )}
      </div>
    </div>
  )
}

function ToolCallBlock({ tool }) {
  const [open, setOpen] = useState(false)
  const running = tool.result == null
  const argsText = formatArgs(tool.args)
  return (
    <div className="tool-call">
      <button
        type="button"
        className="tool-call-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <WrenchIcon />
        <span className="tool-title">
          Called Tool: {tool.display || tool.name}
          {running && <span className="tool-running"> · Running…</span>}
        </span>
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="tool-body">
          {argsText && (
            <div className="tool-section">
              <div className="tool-section-label">Input</div>
              <pre className="tool-args">{argsText}</pre>
            </div>
          )}
          <div className="tool-section">
            <div className="tool-section-label">Result</div>
            <div className="tool-result">
              {imageResultSummary(tool) ?? <EmojiText text={tool.result ?? 'Running…'} />}
              {tool.ok === false && ' (failed)'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ThinkingBlock({ parts: segmentParts, streaming }) {
  const [open, setOpen] = useState(false)
  // Draft calls are surfaced as their own card below the message, so hide the
  // raw tool block to avoid showing the same email twice.
  const parts = segmentParts.filter(
    (p) => !(p.type === 'tool' && draftFromTool(p)),
  )
  if (parts.length === 0) return null

  // Each thinking block carries its own duration (stamped on its parts when
  // the block closes — see backend/app/main.py). A block with no stamp yet is
  // still being thought through, so it reads "Thinking...".
  const stamped = parts.find((p) => p.seconds != null)
  const done = stamped != null || !streaming
  const label = !done
    ? 'Thinking...'
    : stamped
      ? formatThought(stamped.seconds)
      : 'Thought'

  return (
    <div className="thinking">
      <button
        type="button"
        className="thinking-head"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span>{label}</span>
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="thinking-body">
          {parts.map((p, i) =>
            p.type === 'tool' ? (
              <ToolCallBlock key={p.id ?? `tool-${i}`} tool={p} />
            ) : (
              <p key={`r-${i}`} className="thinking-reasoning">
                <EmojiText text={p.text ?? ''} />
              </p>
            ),
          )}
        </div>
      )}
    </div>
  )
}

function CopyMessageButton({ text }) {
  const [copied, setCopied] = useState(false)
  const timer = useRef(null)

  useEffect(() => () => clearTimeout(timer.current), [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.setAttribute('readonly', '')
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      try {
        document.execCommand('copy')
      } finally {
        ta.remove()
      }
    }
    setCopied(true)
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setCopied(false), 1200)
  }

  return (
    <button
      type="button"
      className={`copy-msg${copied ? ' copied' : ''}`}
      onClick={copy}
      aria-label={copied ? 'Copied' : 'Copy message'}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      <span aria-live="polite">{copied ? 'Copied!' : 'Copy message'}</span>
    </button>
  )
}

function RetryMessageButton({ onRetry }) {
  return (
    <button
      type="button"
      className="copy-msg"
      onClick={onRetry}
      aria-label="Try again now"
    >
      <RetryIcon />
      <span>Try again now</span>
    </button>
  )
}

export function ChatWindow({ chat, user, isStreaming, draft, onDraft, onSend, onRetry, onDraftSent, inputRef, effort, onEffort, mode, subject, onMode }) {
  const listRef = useRef(null)
  const composerRef = useRef(null)
  const setComposer = useCallback((el) => {
    composerRef.current = el
    if (inputRef) inputRef.current = el
  }, [inputRef])
  const scrollSig = chat.messages.map((m) => {
    const images = (m.parts ?? []).filter((p) => p.type === 'image').map((p) => p.imageId).join(',')
    return `${m.role}${m.content?.length ?? 0}${images}`
  }).join('|')

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [isStreaming, scrollSig])

  useEffect(() => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
    const line = parseFloat(getComputedStyle(el).lineHeight) || 24
    const multi = el.scrollHeight > line * 1.5 || el.value.includes('\n')
    el.parentElement?.classList.toggle('expanded', multi)
  }, [draft])

  const handleComposerKeyDown = (e) => {
    if (e.key !== 'Enter') return
    // Let IME candidate selection finish before acting on Enter.
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return
    // Shift+Enter and touch keyboards insert a line break instead of sending.
    if (e.shiftKey || isTouchKeyboard()) return
    e.preventDefault()
    if (draft.trim() && !isStreaming) onSend()
  }

  return (
    <>
      <div className="message-list" ref={listRef} aria-label={`Conversation: ${chat.title}`}>
        {chat.messages.length === 0 && !isStreaming && (
          <div className="welcome" role="status">
            <img className="welcome-hero" src="/plates/welcome-hero.png" alt="Ballerina Cappuccina waving hello" />
            <h1 className="welcome-title">Hi! I'm Ballerina Cappuccina</h1>
            {mode === 'tutor' ? (
              <p className="welcome-sub">Pointe shoes on — tell me what you'd like to learn</p>
            ) : (
              <p className="welcome-sub">Your AI chat friend — ask me anything, or let's learn together</p>
            )}
          </div>
        )}
        {chat.messages.map((m, i) => {
          const prev = chat.messages[i - 1]
          const startsRun = !prev || prev.role !== m.role
          const awaitingReply = isStreaming && i === chat.messages.length - 1 && !m.content
          const isActiveStream = isStreaming && i === chat.messages.length - 1
          const toolParts =
            m.role === 'assistant'
              ? (Array.isArray(m.parts) && m.parts.length ? m.parts : m.tools ?? [])
              : []
          const drafts = toolParts.map(draftFromTool).filter(Boolean)
          const images = toolParts.map(imageFromPart).filter(Boolean)
          return (
            <div key={i} className={`msg-row ${m.role}${startsRun ? ' starts' : ''}`}>
              {m.role === 'assistant' ? (
                <div className="msg-avatar">
                  <img src="/plates/msg-avatar.png" alt="" />
                </div>
              ) : (
                <div className="msg-avatar user" aria-hidden="true">
                  {user?.avatarUrl ? (
                    <img src={user.avatarUrl} alt="" referrerPolicy="no-referrer" />
                  ) : (
                    (user?.displayName || '?').slice(0, 1).toUpperCase()
                  )}
                </div>
              )}
              {m.role === 'assistant' ? (
                <div className="assistant-col">
                  {messageSegments(m).map((seg, si) =>
                    seg.type === 'thinking' ? (
                      <ThinkingBlock
                        key={`think-${si}`}
                        parts={seg.parts}
                        streaming={isActiveStream}
                      />
                    ) : (
                      <div key={`text-${si}`} className="bubble">
                        <Markdown text={seg.text} />
                      </div>
                    ),
                  )}
                  {awaitingReply && messageSegments(m).length === 0 && (
                    <div className="bubble bubble-waiting">
                      <StreamingDots />
                    </div>
                  )}
                  {m.content && !isActiveStream && (m.failed
                    ? <RetryMessageButton onRetry={onRetry} />
                    : <CopyMessageButton text={m.content} />)}
                  {images.map((img) => (
                    <ImageCard key={img.imageId} image={img} />
                  ))}
                  {!isActiveStream && drafts.map((d, di) => (
                    <DraftCard
                      key={d.draft_id ?? di}
                      draft={d}
                      onSent={onDraftSent}
                    />
                  ))}
                </div>
              ) : (
                <div className="bubble">
                  <p>
                    <EmojiText text={m.content || '…'} />
                  </p>
                </div>
              )}
            </div>
          )
        })}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          onSend()
        }}
      >
        <div className="composer-row">
          <div className="input">
            <textarea
              rows={1}
              placeholder={mode === 'tutor' ? 'What shall we learn today?' : 'Ask me anything...'}
              aria-label="Message Cappuccina"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              onKeyDown={handleComposerKeyDown}
              ref={setComposer}
            />
          </div>
          <button className="send" type="submit" aria-label="Send message" disabled={!draft.trim() || isStreaming}>
            <SendIcon />
          </button>
        </div>
        <div className="composer-meta">
          <ModeSwitch mode={mode} onMode={onMode} disabled={isStreaming} />
          <EffortSelector effort={effort} onChange={onEffort} disabled={isStreaming} />
        </div>
      </form>
    </>
  )
}
