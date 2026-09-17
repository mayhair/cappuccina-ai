import { useEffect, useRef, useState } from 'react'
import { PersonIcon, SendIcon, ChevronIcon, WrenchIcon, CopyIcon, CheckIcon, RetryIcon, MailIcon, PencilIcon } from './Icons'
import { EffortSelector } from './EffortSelector'
import { ModeSwitch } from './ModeSwitch'
import { Markdown } from '../lib/markdown'
import { EmojiText } from '../lib/emoji'
import { sendDraft, updateDraft } from '../api/gmailClient'

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
  // Ordered interleaving of reasoning + tool calls. Falls back to the legacy
  // grouped shape (reasoning blob, then tools) for messages stored before parts.
  if (Array.isArray(message.parts) && message.parts.length > 0) return message.parts
  const out = []
  const reasoning = message.reasoning ?? ''
  const tools = message.tools ?? []
  if (reasoning) out.push({ type: 'reasoning', text: reasoning })
  for (const t of tools) out.push({ type: 'tool', ...t })
  return out
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
            role="button"
            tabIndex={0}
            title="Click to edit"
            onClick={startEditing}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                startEditing()
              }
            }}
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
              <EmojiText text={tool.result ?? 'Running…'} />
              {tool.ok === false && ' (failed)'}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ThinkingBlock({ message, streaming }) {
  const [open, setOpen] = useState(false)
  // Draft calls are surfaced as their own card below the message, so hide the
  // raw tool block to avoid showing the same email twice.
  const parts = thinkingParts(message).filter(
    (p) => !(p.type === 'tool' && draftFromTool(p)),
  )
  if (parts.length === 0) return null

  // thinkingDone/thoughtSeconds are set when the live stream finishes. After a
  // reload the stream is over by definition, so anything not actively
  // streaming counts as done (rows stored before durations were persisted
  // simply show "Thought" without a duration).
  const done = message.thinkingDone || !streaming
  const label = !done
    ? 'Thinking...'
    : message.thoughtSeconds != null
      ? formatThought(message.thoughtSeconds)
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

export function ChatWindow({ chat, isStreaming, draft, onDraft, onSend, onRetry, onDraftSent, inputRef, effort, onEffort, mode, subject, onMode }) {
  const listRef = useRef(null)
  const scrollSig = chat.messages.map((m) => `${m.role}${m.content?.length ?? 0}`).join('|')

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [isStreaming, scrollSig])

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
          return (
            <div key={i} className={`msg-row ${m.role}${startsRun ? ' starts' : ''}`}>
              {m.role === 'assistant' ? (
                <div className="msg-avatar">
                  <img src="/plates/msg-avatar.png" alt="" />
                </div>
              ) : (
                <div className="msg-avatar user" aria-hidden="true">
                  <PersonIcon />
                </div>
              )}
              {m.role === 'assistant' ? (
                <div className="assistant-col">
                  <ThinkingBlock message={m} streaming={isActiveStream} />
                  <div className={`bubble${awaitingReply ? ' bubble-waiting' : ''}`}>
                    {m.content ? (
                      <Markdown text={m.content} />
                    ) : awaitingReply ? (
                      <StreamingDots />
                    ) : (
                      <p>…</p>
                    )}
                  </div>
                  {m.content && !isActiveStream && (m.failed
                    ? <RetryMessageButton onRetry={onRetry} />
                    : <CopyMessageButton text={m.content} />)}
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
            <input
              type="text"
              placeholder={mode === 'tutor' ? 'What shall we learn today?' : 'Ask me anything...'}
              aria-label="Message Cappuccina"
              value={draft}
              onChange={(e) => onDraft(e.target.value)}
              ref={inputRef}
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
