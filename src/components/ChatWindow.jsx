import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { SendIcon, ChevronIcon, WrenchIcon, CopyIcon, CheckIcon, RetryIcon, MailIcon, PencilIcon, PaperclipIcon, FileIcon, CalendarIcon, MicIcon } from './Icons'
import { EffortSelector } from './EffortSelector'
import { ModeSwitch } from './ModeSwitch'
import { Markdown } from '../lib/markdown'
import { EmojiText } from '../lib/emoji'
import { branchPosition } from '../lib/branching'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { sendDraft, updateDraft } from '../api/gmailClient'
import { deleteEvent } from '../api/calendarClient'

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
  // Drop whitespace-only text parts: the model sometimes streams a lone space
  // before a tool call, which would otherwise render as an empty bubble.
  const raw = thinkingParts(message).filter(
    (p) => p.type !== 'text' || (p.text ?? '').trim() !== '',
  )
  const content = message.content ?? ''
  const hasTextPart = raw.some((p) => p.type === 'text')
  const parts = hasTextPart
    ? raw
    : content.trim()
      ? [...raw, { type: 'text', text: content }]
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

// Colours a pretty-printed JSON blob by splitting it into React element spans
// (never innerHTML) so untrusted model output stays injection-safe. Anything
// that is not a quoted string, number, or boolean/null literal is left as
// plain text, so non-JSON args still render legibly.
const JSON_TOKEN =
  /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|(true|false|null)/g

function highlightJson(text) {
  const out = []
  let last = 0
  let n = 0
  for (const m of text.matchAll(JSON_TOKEN)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const cls =
      m[1] !== undefined ? 'json-key'
        : m[2] !== undefined ? 'json-string'
          : m[3] !== undefined ? 'json-number'
            : 'json-literal'
    out.push(
      <span key={`j-${n++}`} className={cls}>
        {m[0]}
      </span>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
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

// A successful propose_event_deletion tool result carries JSON the UI turns
// into a card with an explicit Delete button. The assistant never deletes on
// its own — this button is the only way an event is removed.
function deleteFromTool(tool) {
  if (tool?.name !== 'propose_event_deletion' || typeof tool.result !== 'string') {
    return null
  }
  try {
    const data = JSON.parse(tool.result)
    if (!(data?.status === 'delete_proposed' && data.event_id)) return null
    // `deleted` is stamped by the server on reload and by the client after
    // confirming.
    return { ...data, deleted: tool.deleted === true || data.deleted === true }
  } catch {
    return null
  }
}

// A generate_image/image_edit result is a machine-readable pointer at the
// saved picture (see backend/app/main.py); the picture itself renders as an
// image card below the message, so the thinking panel only needs a one-liner.
function imageFromPart(part) {
  if (part?.type !== 'image' || typeof part.imageId !== 'string' || !part.imageId) {
    return null
  }
  return part
}

const IMAGE_TOOLS = ['generate_image', 'image_edit']
const IMAGE_STATUSES = ['image_generated', 'image_edited']

function imageResultSummary(tool) {
  if (!IMAGE_TOOLS.includes(tool?.name) || typeof tool.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    if (!IMAGE_STATUSES.includes(data?.status) || !data.image_id) return null
    return tool.name === 'image_edit'
      ? 'Edit ready — see below ✨'
      : 'Picture ready — see below ✨'
  } catch {
    return null
  }
}

// Accepted by the picker; the server rejects anything else with a clear error.
const ATTACH_ACCEPT = [
  'image/png', 'image/jpeg', 'image/webp',
  '.pdf', '.docx', '.pptx', '.xlsx',
  '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.log', '.rst',
  '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css', '.xml',
  '.yaml', '.yml', '.ini', '.cfg', '.toml', '.sql', '.sh', '.java', '.c',
  '.h', '.cpp', '.go', '.rs', '.rb', '.php', '.tex',
].join(',')

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// An attachment part references a server-stored file by id (see
// backend/app/main.py); the bytes/text never travel with the message.
function attachmentFromPart(part) {
  if (part?.type !== 'attachment' || !part.attachmentId) return null
  return {
    id: part.attachmentId,
    kind: part.kind,
    name: part.name || 'file',
    mime: part.mime || '',
    size: part.size ?? 0,
  }
}

function AttachmentPreview({ attachment }) {
  const href = `/api/v1/attachments/${attachment.id}`
  if (attachment.kind === 'image') {
    return (
      <a className="msg-attachment image" href={href} target="_blank" rel="noreferrer">
        <img src={href} alt={attachment.name} loading="lazy" />
      </a>
    )
  }
  return (
    <a className="msg-attachment file" href={href} target="_blank" rel="noreferrer">
      <FileIcon />
      <span className="msg-attachment-name">{attachment.name}</span>
      {attachment.size ? <span className="msg-attachment-size">{formatSize(attachment.size)}</span> : null}
    </a>
  )
}

function AttachmentChip({ attachment, onRemove }) {
  const href = `/api/v1/attachments/${attachment.id}`
  return (
    <div className={`attach-chip${attachment.kind === 'image' ? ' image' : ''}`}>
      {attachment.kind === 'image' ? (
        <img src={href} alt="" />
      ) : (
        <FileIcon />
      )}
      <span className="attach-chip-name" title={attachment.name}>{attachment.name}</span>
      <button
        type="button"
        className="attach-chip-remove"
        onClick={() => onRemove(attachment.id)}
        aria-label={`Remove ${attachment.name}`}
      >
        ×
      </button>
    </div>
  )
}

function ImageCard({ image }) {
  const [open, setOpen] = useState(false)
  const src = `/api/v1/images/${image.imageId}`
  const alt = image.prompt ? `Picture Cappuccina drew: ${image.prompt}` : 'Picture Cappuccina drew'
  const transparent = image.transparent === true

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
          className={transparent ? 'image-transparent' : undefined}
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
            <img
              className={transparent ? 'image-transparent' : undefined}
              src={src}
              alt={alt}
              onClick={(e) => e.stopPropagation()}
            />
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

function EventDeleteCard({ proposal, onDeleted }) {
  // A previously confirmed deletion (from a reload) starts in the done state.
  const [state, setState] = useState(proposal.deleted ? 'deleted' : 'idle')
  const [message, setMessage] = useState('')

  const handleError = (err) => {
    if (
      err.status === 409 &&
      /already gone|no longer exists|deleted/i.test(err.message)
    ) {
      setState('deleted')
      onDeleted?.(proposal.event_id)
      return
    }
    setState('error')
    setMessage(err.message || 'Something went wrong — try again')
  }

  const remove = async () => {
    if (state === 'deleting' || state === 'deleted') return
    setState('deleting')
    setMessage('')
    try {
      await deleteEvent(proposal.event_id)
      setState('deleted')
      onDeleted?.(proposal.event_id)
    } catch (err) {
      handleError(err)
    }
  }

  return (
    <div className="draft-card">
      <div className="draft-card-head">
        <CalendarIcon />
        Delete this event?
      </div>

      <div className="draft-fields">
        <div><b>Event</b>{proposal.title}</div>
        <div><b>When</b>{proposal.when}</div>
        {proposal.location && <div><b>Where</b>{proposal.location}</div>}
      </div>

      {state === 'error' && (
        <p className="draft-note error draft-error">{message}</p>
      )}

      <div className="draft-actions">
        {state === 'deleted' ? (
          <span className="draft-note ok"><CheckIcon /> Deleted</span>
        ) : (
          <>
            <button
              type="button"
              className="draft-send danger"
              onClick={remove}
              disabled={state === 'deleting'}
            >
              {state === 'deleting' ? 'Deleting…' : 'Delete event'}
            </button>
            <span className="draft-note">Cappuccina won&rsquo;t delete it without you</span>
          </>
        )}
      </div>
    </div>
  )
}

function ToolCallBlock({ tool, onManualToggle }) {
  const [open, setOpen] = useState(false)
  const running = tool.result == null
  const failed = tool.ok === false
  const argsText = formatArgs(tool.args)
  return (
    <div className="tool-call">
      <button
        type="button"
        className="tool-call-head"
        onClick={() => {
          onManualToggle?.()
          setOpen((o) => !o)
        }}
        aria-expanded={open}
      >
        <span className="tool-badge" aria-hidden="true">
          <WrenchIcon />
        </span>
        <span className="tool-title">{tool.display || tool.name}</span>
        {running ? (
          <span className="tool-status running">
            <StreamingDots label={`${tool.display || tool.name} is running`} />
            Running
          </span>
        ) : failed ? (
          <span className="tool-status failed">Failed</span>
        ) : (
          <span className="tool-status done">
            <CheckIcon />
            Done
          </span>
        )}
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="tool-body">
          {argsText && (
            <div className="tool-section">
              <div className="tool-section-label">Input</div>
              <pre className="tool-args">{highlightJson(argsText)}</pre>
            </div>
          )}
          <div className="tool-section">
            <div className="tool-section-label">Result</div>
            <div className="tool-result">
              {imageResultSummary(tool) ?? <EmojiText text={tool.result ?? 'Running…'} />}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ThinkingBlock({ parts: segmentParts, streaming, fallbackSeconds = null }) {
  // Draft and deletion calls are surfaced as their own cards below the
  // message, so hide the raw tool blocks to avoid showing them twice.
  const parts = segmentParts.filter(
    (p) => !(p.type === 'tool' && (draftFromTool(p) || deleteFromTool(p))),
  )

  // Each thinking block carries its own duration (stamped on its parts when
  // the block closes — see backend/app/main.py). Messages stored before
  // per-part timing, and replies from the non-streaming endpoint, only carry a
  // message-level duration, so fall back to it when no part is stamped. A
  // block with neither while still streaming is still being thought through.
  const stamped = parts.find((p) => p.seconds != null)
  const seconds = stamped?.seconds ?? fallbackSeconds
  const done = seconds != null || !streaming
  const label = !done
    ? 'Thinking...'
    : seconds != null
      ? formatThought(seconds)
      : 'Thought'

  // Auto-expand while thinking runs, auto-close once it's done. Any manual
  // click on the block head or on a tool call inside it takes over for good.
  const [open, setOpen] = useState(!done)
  const [manual, setManual] = useState(false)
  useEffect(() => {
    if (!manual) setOpen(!done)
  }, [done, manual])

  if (parts.length === 0) return null

  return (
    <div className="thinking">
      <button
        type="button"
        className="thinking-head"
        onClick={() => {
          setManual(true)
          setOpen((o) => !o)
        }}
        aria-expanded={open}
      >
        <span>{label}</span>
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="thinking-body">
          {parts.map((p, i) =>
            p.type === 'tool' ? (
              <ToolCallBlock
                key={p.id ?? `tool-${i}`}
                tool={p}
                onManualToggle={() => setManual(true)}
              />
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

function RegenerateMessageButton({ onRegenerate }) {
  return (
    <button
      type="button"
      className="copy-msg"
      onClick={onRegenerate}
      aria-label="Regenerate"
    >
      <RetryIcon />
      <span>Regenerate</span>
    </button>
  )
}

// Arrows + "x/y" version switcher, shown only when a message has branches.
function BranchNav({ index, total, onPrev, onNext }) {
  return (
    <div className="branch-nav" role="group" aria-label={`Version ${index + 1} of ${total}`}>
      <button
        type="button"
        className="branch-arrow prev"
        onClick={onPrev}
        disabled={index <= 0}
        aria-label="Previous version"
      >
        <ChevronIcon />
      </button>
      <span className="branch-count" aria-live="polite">{index + 1}/{total}</span>
      <button
        type="button"
        className="branch-arrow next"
        onClick={onNext}
        disabled={index >= total - 1}
        aria-label="Next version"
      >
        <ChevronIcon />
      </button>
    </div>
  )
}

function EditMessageForm({ value, onChange, onSend, onCancel }) {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [])
  return (
    <div className="edit-msg">
      <textarea
        ref={ref}
        className="edit-msg-input"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={Math.min(8, Math.max(2, value.split('\n').length))}
      />
      <div className="edit-msg-actions">
        <button type="button" className="edit-msg-cancel" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="edit-msg-send"
          onClick={onSend}
          disabled={!value.trim()}
        >
          Send
        </button>
      </div>
    </div>
  )
}

function MessageRow({ message, prev, isActiveStream, user, onRetry, onDraftSent, onEventDeleted, branch, onEdit, onRegenerate, onNavigateBranch }) {
  const m = message
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  // Leaving this version (branch switch) closes the editor.
  useEffect(() => { setEditing(false) }, [m.id])
  const canBranch = (branch?.total ?? 0) > 1
  const startsRun = !prev || prev.role !== m.role
  const awaitingReply = isActiveStream && !m.content
  const toolParts =
    m.role === 'assistant'
      ? (Array.isArray(m.parts) && m.parts.length ? m.parts : m.tools ?? [])
      : []
  const drafts = toolParts.map(draftFromTool).filter(Boolean)
  const proposals = toolParts.map(deleteFromTool).filter(Boolean)
  const images = toolParts.map(imageFromPart).filter(Boolean)
  const userAttachments =
    m.role === 'user'
      ? (m.parts ?? []).map(attachmentFromPart).filter(Boolean)
      : []
  const segments = m.role === 'assistant' ? messageSegments(m) : []
  // Attachments stay visible while editing so the user still sees what the
  // message carried (only the text is being changed).
  const attachmentsBlock = userAttachments.length > 0 ? (
    <div className="msg-attachments">
      {userAttachments.map((a, ai) => (
        <AttachmentPreview key={a.id ?? ai} attachment={a} />
      ))}
    </div>
  ) : null
  // A message-level duration is only unambiguous when the whole reply
  // is a single thinking block; with several blocks each carries its
  // own stamp, so don't smear the total across all of them.
  const thinkingCount = segments.filter((s) => s.type === 'thinking').length
  return (
    <div className={`msg-row ${m.role}${startsRun ? ' starts' : ''}`}>
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
          {segments.map((seg, si) =>
            seg.type === 'thinking' ? (
              <ThinkingBlock
                key={`think-${si}`}
                parts={seg.parts}
                streaming={isActiveStream}
                fallbackSeconds={thinkingCount === 1 ? m.thoughtSeconds : null}
              />
            ) : (
              <div key={`text-${si}`} className="bubble">
                <Markdown text={seg.text} />
              </div>
            ),
          )}
          {awaitingReply && segments.length === 0 && (
            <div className="bubble bubble-waiting">
              <StreamingDots />
            </div>
          )}
          {m.content && !isActiveStream && (
            <div className="msg-actions">
              {m.failed
                ? <RetryMessageButton onRetry={onRetry} />
                : <CopyMessageButton text={m.content} />}
              {!m.failed && <RegenerateMessageButton onRegenerate={() => onRegenerate?.(m.id)} />}
              {canBranch && (
                <BranchNav
                  index={branch.index}
                  total={branch.total}
                  onPrev={() => onNavigateBranch?.(m.id, -1)}
                  onNext={() => onNavigateBranch?.(m.id, 1)}
                />
              )}
            </div>
          )}
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
          {!isActiveStream && proposals.map((p, pi) => (
            <EventDeleteCard
              key={p.event_id ?? pi}
              proposal={p}
              onDeleted={onEventDeleted}
            />
          ))}
        </div>
      ) : (
        <div className="user-col">
          {editing ? (
            <>
              {attachmentsBlock}
              <EditMessageForm
                value={editText}
                onChange={setEditText}
                onCancel={() => setEditing(false)}
                onSend={() => {
                  const next = editText.trim()
                  if (!next) return
                  onEdit?.(m.id, next)
                  setEditing(false)
                }}
              />
            </>
          ) : (
            <div className="bubble">
              {attachmentsBlock}
              {(m.content || userAttachments.length === 0) && (
                <p>
                  <EmojiText text={m.content || '…'} />
                </p>
              )}
            </div>
          )}
          {!isActiveStream && !editing && (
            <div className="msg-actions">
              <button
                type="button"
                className="copy-msg"
                onClick={() => {
                  setEditText(m.content ?? '')
                  setEditing(true)
                }}
                aria-label="Edit message"
              >
                <PencilIcon />
                <span>Edit</span>
              </button>
              {canBranch && (
                <BranchNav
                  index={branch.index}
                  total={branch.total}
                  onPrev={() => onNavigateBranch?.(m.id, -1)}
                  onNext={() => onNavigateBranch?.(m.id, 1)}
                />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Composer({
  draft,
  onDraft,
  onSend,
  isStreaming,
  uploading,
  attachments,
  attachError,
  onAttach,
  onRemoveAttachment,
  mode,
  onMode,
  effort,
  onEffort,
  inputRef,
}) {
  const composerRef = useRef(null)
  const fileRef = useRef(null)
  const setComposer = useCallback((el) => {
    composerRef.current = el
    if (inputRef) inputRef.current = el
  }, [inputRef])

  const handleFiles = (e) => {
    const files = e.target.files
    if (files?.length) onAttach?.(files)
    e.target.value = ''
  }

  // Voice input: finals are appended to the draft so the kid reviews (and can
  // fix misheard words) before sending; interim text only shows live.
  const appendTranscript = useCallback(
    (text) => {
      const chunk = text.trim()
      if (!chunk) return
      onDraft?.((prev) => {
        const base = prev ?? ''
        return base ? `${base}${/\s$/.test(base) ? '' : ' '}${chunk}` : chunk
      })
    },
    [onDraft],
  )
  const voice = useVoiceInput({ onTranscript: appendTranscript, disabled: isStreaming || uploading })

  useEffect(() => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
    const line = parseFloat(getComputedStyle(el).lineHeight) || 24
    const multi = el.scrollHeight > line * 1.5 || el.value.includes('\n')
    el.parentElement?.classList.toggle('expanded', multi)
  }, [draft, voice.interim])

  const voiceSuffix = voice.listening && voice.interim ? voice.interim : ''
  const composerValue = voiceSuffix
    ? `${draft}${draft && !/\s$/.test(draft) ? ' ' : ''}${voiceSuffix}`
    : draft

  const handleDraftChange = (value) => {
    // Typing takes over from dictation.
    if (voice.listening) voice.stop()
    onDraft?.(value)
  }

  const submit = () => {
    if (voice.listening) voice.stop()
    onSend()
  }

  const handleComposerKeyDown = (e) => {
    if (e.key === 'Escape' && voice.listening) {
      e.preventDefault()
      voice.stop()
      return
    }
    if (e.key !== 'Enter') return
    // Let IME candidate selection finish before acting on Enter.
    if (e.nativeEvent?.isComposing || e.keyCode === 229) return
    // Shift+Enter and touch keyboards insert a line break instead of sending.
    if (e.shiftKey || isTouchKeyboard()) return
    e.preventDefault()
    if ((draft.trim() || attachments.length > 0) && !isStreaming && !uploading) submit()
  }

  return (
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        {(attachments.length > 0 || uploading) && (
          <div className="attach-tray">
            {attachments.map((a) => (
              <AttachmentChip key={a.id} attachment={a} onRemove={onRemoveAttachment} />
            ))}
            {uploading && <span className="attach-uploading">Uploading…</span>}
          </div>
        )}
        {attachError && <p className="attach-error" role="alert">{attachError}</p>}
        {voice.error && <p className="attach-error" role="alert">{voice.error}</p>}
        <div className="composer-row">
          <div className={`input${voice.listening ? ' listening' : ''}`}>
            <textarea
              rows={1}
              placeholder={mode === 'tutor' ? 'What shall we learn today?' : 'Ask me anything...'}
              aria-label="Message Cappuccina"
              value={composerValue}
              onChange={(e) => handleDraftChange(e.target.value)}
              onKeyDown={handleComposerKeyDown}
              ref={setComposer}
            />
          </div>
          <input
            ref={fileRef}
            type="file"
            className="attach-input"
            multiple
            accept={ATTACH_ACCEPT}
            onChange={handleFiles}
            tabIndex={-1}
          />
          {voice.supported && (
            <button
              type="button"
              className={`mic has-tip tip-up${voice.listening ? ' rec' : ''}`}
              data-tip={voice.listening ? 'Listening… tap to stop' : 'Speak your message'}
              aria-label={voice.listening ? 'Stop voice input' : 'Speak your message'}
              aria-pressed={voice.listening}
              onClick={voice.toggle}
              disabled={isStreaming || uploading}
            >
              <MicIcon />
            </button>
          )}
          <button
            type="button"
            className="attach has-tip tip-up"
            data-tip="Attach files..."
            aria-label="Attach files..."
            onClick={() => fileRef.current?.click()}
            disabled={isStreaming || uploading}
          >
            <PaperclipIcon />
          </button>
          <button
            className="send"
            type="submit"
            aria-label="Send message"
            disabled={(!draft.trim() && attachments.length === 0) || isStreaming || uploading}
          >
            <SendIcon />
          </button>
        </div>
        <div className="composer-meta">
          <ModeSwitch mode={mode} onMode={onMode} disabled={isStreaming} />
          <EffortSelector effort={effort} onChange={onEffort} disabled={isStreaming} />
        </div>
      </form>
  )
}

export function ChatWindow({ chat, user, isStreaming, draft, onDraft, onSend, onRetry, onEdit, onRegenerate, onNavigateBranch, onDraftSent, onEventDeleted, inputRef, effort, onEffort, mode, onMode, attachments = [], uploading = false, attachError = '', onAttach, onRemoveAttachment }) {
  const listRef = useRef(null)

  const canAttach = !isStreaming && !uploading

  // Window-level drag & drop. dragenter/dragleave fire for every child element
  // crossed, so a depth counter keeps the overlay visible until we really leave.
  // A dragover watchdog is the safety net: touch browsers often never fire
  // dragleave/drop when a drag is cancelled, which otherwise strands the overlay.
  const dragDepth = useRef(0)
  const [dragActive, setDragActive] = useState(false)

  useEffect(() => {
    const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files')
    let hideTimer = null
    const hide = () => {
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
      dragDepth.current = 0
      setDragActive(false)
    }
    const onDragEnter = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      if (!canAttach) return
      dragDepth.current += 1
      setDragActive(true)
    }
    const onDragOver = (e) => {
      // Always prevent default for files, or the browser would open the file
      // instead of letting us handle the drop.
      if (!hasFiles(e)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = canAttach ? 'copy' : 'none'
      if (!canAttach) return
      // dragover fires repeatedly while a drag is live (~every 350ms), so a
      // timeout that outlives that cadence hides the overlay once the drag
      // ends without a drop or dragleave.
      setDragActive(true)
      if (hideTimer) clearTimeout(hideTimer)
      hideTimer = setTimeout(hide, 600)
    }
    const onDragLeave = (e) => {
      if (!hasFiles(e)) return
      // relatedTarget is null when the pointer left the window entirely.
      if (e.relatedTarget == null) { hide(); return }
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) hide()
    }
    const onDrop = (e) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      hide()
      if (!canAttach) return
      const files = e.dataTransfer?.files
      if (files?.length) onAttach?.(files)
    }
    const onVisibility = () => { if (document.hidden) hide() }
    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    window.addEventListener('dragend', hide)
    window.addEventListener('blur', hide)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      if (hideTimer) clearTimeout(hideTimer)
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDrop)
      window.removeEventListener('dragend', hide)
      window.removeEventListener('blur', hide)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [canAttach, onAttach])

  // If the composer becomes unable to attach mid-drag, drop the overlay.
  useEffect(() => {
    if (!canAttach) {
      dragDepth.current = 0
      setDragActive(false)
    }
  }, [canAttach])

  // Paste files from the clipboard (e.g. a copied screenshot). Text pastes fall
  // through to the browser so the composer behaves normally.
  useEffect(() => {
    const onPaste = (e) => {
      const files = e.clipboardData?.files
      if (!files?.length || !canAttach) return
      e.preventDefault()
      onAttach?.(files)
    }
    window.addEventListener('paste', onPaste)
    return () => window.removeEventListener('paste', onPaste)
  }, [canAttach, onAttach])

  const scrollSig = chat.messages.map((m) => {
    const images = (m.parts ?? []).filter((p) => p.type === 'image').map((p) => p.imageId).join(',')
    return `${m.role}${m.content?.length ?? 0}${images}`
  }).join('|')

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
        {chat.messages.map((m, i) => (
          <MessageRow
            key={m.id ?? i}
            message={m}
            prev={chat.messages[i - 1]}
            isActiveStream={isStreaming && i === chat.messages.length - 1}
            user={user}
            branch={branchPosition(chat.allMessages ?? chat.messages, m)}
            onRetry={onRetry}
            onEdit={onEdit}
            onRegenerate={onRegenerate}
            onNavigateBranch={onNavigateBranch}
            onDraftSent={onDraftSent}
            onEventDeleted={onEventDeleted}
          />
        ))}
      </div>

      <Composer
        draft={draft}
        onDraft={onDraft}
        onSend={onSend}
        isStreaming={isStreaming}
        uploading={uploading}
        attachments={attachments}
        attachError={attachError}
        onAttach={onAttach}
        onRemoveAttachment={onRemoveAttachment}
        mode={mode}
        onMode={onMode}
        effort={effort}
        onEffort={onEffort}
        inputRef={inputRef}
      />

      {dragActive && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-overlay-card">
            <PaperclipIcon />
            <span>Drop files to attach</span>
          </div>
        </div>
      )}
    </>
  )
}
