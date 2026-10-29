import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { SendIcon, ChevronIcon, WrenchIcon, CopyIcon, CheckIcon, RetryIcon, MailIcon, PencilIcon, PaperclipIcon, FileIcon, CalendarIcon, MicIcon, WindowIcon, BrainIcon } from './Icons'
import { EffortSelector } from './EffortSelector'
import { ModeSwitch } from './ModeSwitch'
import { StoryBar, StoryChoices, StoryStart, choicesFromPart } from './Story'
import { isOoc, isOocReply } from '../lib/modes'
import { Markdown } from '../lib/markdown'
import { highlightJson } from '../lib/highlightCode'
import { EmojiText } from '../lib/emoji'
import { branchPosition } from '../lib/branching'
import { useVoiceInput } from '../hooks/useVoiceInput'
import { sendDraft, updateDraft } from '../api/gmailClient'
import { deleteEvent } from '../api/calendarClient'
import { formatBytes } from '../lib/format'
import { createMemory, deleteMemory, updateMemory } from '../api/memoryClient'
import { useLanguage } from '../i18n/LanguageProvider'

const isTouchKeyboard = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(hover: none) and (pointer: coarse)').matches

export function StreamingDots({ label }) {
  const { t } = useLanguage()
  return (
    <span className="streaming" role="status" aria-label={label ?? t('chat.typing')}>
      <i /><i /><i />
    </span>
  )
}

function formatThought(seconds, t) {
  const s = Math.round(seconds * 10) / 10
  const shown = Number.isInteger(s) ? String(s) : s.toFixed(1)
  return t(s === 1 ? 'chat.thoughtOne' : 'chat.thought', { seconds: shown })
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
  // before a tool call, which would otherwise render as an empty bubble. Images
  // and webpages render as their own cards below the message, so they never
  // join a thinking block either.
  const raw = thinkingParts(message).filter(
    (p) =>
      p.type !== 'image' &&
      p.type !== 'webpage' &&
      // Story mode's next moves show as buttons under the reply, not a card.
      !(p.type === 'tool' && p.name === 'suggest_actions') &&
      (p.type !== 'text' || (p.text ?? '').trim() !== ''),
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

// A successful save/update/delete memory call carries JSON the UI turns into a
// small card ("Remembered: …") with an Undo button, so every change the
// assistant makes to the user's memory is visible and reversible.
const MEMORY_TOOLS = ['save_memory', 'update_memory', 'delete_memory']
const MEMORY_STATUSES = ['saved', 'updated', 'deleted']

function memoryFromTool(tool) {
  if (!MEMORY_TOOLS.includes(tool?.name) || typeof tool.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    if (!(MEMORY_STATUSES.includes(data?.status) && data.id)) return null
    // `undone` is stamped locally after the user presses Undo.
    return { ...data, tool: tool.name, undone: tool.undone === true }
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

function imageResultSummary(tool, t) {
  if (!IMAGE_TOOLS.includes(tool?.name) || typeof tool.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    if (!IMAGE_STATUSES.includes(data?.status) || !data.image_id) return null
    return tool.name === 'image_edit'
      ? t('chat.editReady')
      : t('chat.pictureReady')
  } catch {
    return null
  }
}

// A webpage tool result is a machine-readable pointer at the chat's webpage
// workspace (see backend/app/main.py); the page renders in the canvas, so the
// thinking panel only needs a one-liner. Any result carrying a webpage_id
// counts — the write, the asset copy and the site command.
function webpageResultSummary(tool, t) {
  if (typeof tool?.result !== 'string') return null
  try {
    const data = JSON.parse(tool.result)
    if (!data?.webpage_id) return null
    if (data.status === 'webpage_file_written' && data.path) {
      return t('chat.savedFile', { path: data.path })
    }
    if (data.status === 'webpage_asset_added' && data.path) {
      return t('chat.addedFile', { path: data.path })
    }
    if (data.status === 'webpage_command_ran') {
      return t('chat.ranCommand')
    }
    return null
  } catch {
    return null
  }
}

// A page the assistant built or changed in this turn, for the card under the
// message. Several writes in one turn share the environment id, so they show
// as one card listing the files.
function webpagesFromParts(parts) {
  const out = []
  for (const part of parts ?? []) {
    if (part?.type !== 'webpage' || !part.webpageId) continue
    const existing = out.find((w) => w.webpageId === part.webpageId)
    if (existing) {
      existing.files = part.files ?? existing.files
      existing.path = part.path ?? existing.path
    } else {
      out.push({ webpageId: part.webpageId, path: part.path, files: [...(part.files ?? [])] })
    }
  }
  return out
}

function WebpageCard({ webpage, onOpen }) {
  const { t } = useLanguage()
  const files = webpage.files ?? []
  return (
    <figure className="webpage-card">
      <div className="webpage-card-head">
        <WindowIcon />
        <span className="webpage-card-title">{t('chat.webpageReady')}</span>
      </div>
      {files.length > 0 && (
        <ul className="webpage-card-files">
          {files.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      )}
      <button type="button" className="webpage-card-open" onClick={onOpen}>
        {t('chat.openCanvas')}
      </button>
    </figure>
  )
}

// Accepted by the picker; the server rejects anything else with a clear error.
const ATTACH_ACCEPT = [
  'image/png', 'image/jpeg', 'image/webp',
  '.pdf', '.docx', '.pptx', '.xlsx',
  '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.log', '.rst',
  '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.htm', '.css', '.xml',
  '.yaml', '.yml', '.ini', '.cfg', '.toml', '.sql', '.sh', '.java', '.c',
  '.h', '.cpp', '.go', '.rs', '.rb', '.php', '.tex',
  '.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.opus',
  '.mp4', '.webm', '.mov',
  '.woff', '.woff2', '.ttf', '.otf', '.ico',
].join(',')

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
      {attachment.size ? <span className="msg-attachment-size">{formatBytes(attachment.size)}</span> : null}
    </a>
  )
}

function AttachmentChip({ attachment, onRemove }) {
  const { t } = useLanguage()
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
        aria-label={t('chat.removeAttachment', { name: attachment.name })}
      >
        ×
      </button>
    </div>
  )
}

function ImageCard({ image }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const src = `/api/v1/images/${image.imageId}`
  const alt = image.prompt ? t('chat.pictureDrewPrompt', { prompt: image.prompt }) : t('chat.pictureDrew')
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
        aria-label={t('chat.viewPicture')}
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
              aria-label={t('chat.closePicture')}
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
  const { t } = useLanguage()
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
    setMessage(err.message || t('settings.error.generic'))
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
        {t('draft.title')}
        {!editing && state !== 'sent' && (
          <button type="button" className="draft-edit" onClick={startEditing}>
            <PencilIcon /> {t('chat.edit')}
          </button>
        )}
      </div>

      {editing ? (
        <div className="draft-edit-form">
          <label className="draft-edit-field">
            <span>{t('draft.to')}</span>
            <input
              type="email"
              value={fields.to}
              onChange={(e) => setFields({ ...fields, to: e.target.value })}
              aria-label={t('draft.recipient')}
            />
          </label>
          <label className="draft-edit-field">
            <span>{t('draft.subject')}</span>
            <input
              type="text"
              value={fields.subject}
              onChange={(e) => setFields({ ...fields, subject: e.target.value })}
              aria-label={t('draft.subject')}
            />
          </label>
          <label className="draft-edit-field">
            <span>{t('draft.message')}</span>
            <textarea
              rows={6}
              value={fields.body}
              onChange={(e) => setFields({ ...fields, body: e.target.value })}
              aria-label={t('draft.body')}
            />
          </label>
        </div>
      ) : (
        <>
          <div className="draft-fields">
            <div><b>{t('draft.to')}</b>{fields.to}</div>
            <div><b>{t('draft.subject')}</b>{fields.subject}</div>
          </div>
          <p
            className="draft-body"
            role={state === 'sent' ? undefined : 'button'}
            tabIndex={state === 'sent' ? undefined : 0}
            title={state === 'sent' ? undefined : t('draft.clickToEdit')}
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
          <span className="draft-note ok"><CheckIcon /> {t('draft.sent')}</span>
        ) : editing ? (
          <>
            <button
              type="button"
              className="draft-send"
              onClick={save}
              disabled={state === 'saving' || !canSubmit || !dirty}
            >
              {state === 'saving' ? t('settings.profile.saving') : t('draft.saveChanges')}
            </button>
            <button
              type="button"
              className="draft-secondary"
              onClick={cancel}
              disabled={state === 'saving'}
            >
              {t('common.cancel')}
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
              {state === 'sending' ? t('draft.sending') : t('draft.send')}
            </button>
            <span className="draft-note">{t('draft.clickMessage')}</span>
          </>
        )}
      </div>
    </div>
  )
}

function EventDeleteCard({ proposal, onDeleted }) {
  const { t } = useLanguage()
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
    setMessage(err.message || t('settings.error.generic'))
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
        {t('event.deleteQ')}
      </div>

      <div className="draft-fields">
        <div><b>{t('event.event')}</b>{proposal.title}</div>
        <div><b>{t('event.when')}</b>{proposal.when}</div>
        {proposal.location && <div><b>{t('event.where')}</b>{proposal.location}</div>}
      </div>

      {state === 'error' && (
        <p className="draft-note error draft-error">{message}</p>
      )}

      <div className="draft-actions">
        {state === 'deleted' ? (
          <span className="draft-note ok"><CheckIcon /> {t('event.deleted')}</span>
        ) : (
          <>
            <button
              type="button"
              className="draft-send danger"
              onClick={remove}
              disabled={state === 'deleting'}
            >
              {state === 'deleting' ? t('event.deleting') : t('event.delete')}
            </button>
            <span className="draft-note">{t('event.note')}</span>
          </>
        )}
      </div>
    </div>
  )
}

const MEMORY_SUMMARY_KEYS = {
  saved: 'chat.memorySaved',
  updated: 'chat.memoryUpdated',
  deleted: 'chat.memoryDeleted',
}

function MemoryCard({ memory, onUndone, onManage }) {
  const { t } = useLanguage()
  const [state, setState] = useState(memory.undone ? 'undone' : 'idle')

  const undo = async () => {
    if (state === 'undoing' || state === 'undone') return
    setState('undoing')
    try {
      if (memory.status === 'saved') await deleteMemory(memory.id)
      else if (memory.status === 'updated') await updateMemory(memory.id, memory.previous)
      else await createMemory(memory.content)
      setState('undone')
      onUndone?.(memory.tool, memory.id)
    } catch (err) {
      if (err.status === 404) {
        // The memory is already gone (removed in Settings), which is what Undo wanted.
        setState('undone')
        onUndone?.(memory.tool, memory.id)
        return
      }
      setState('error')
    }
  }

  return (
    <div className="memory-card">
      <BrainIcon aria-hidden="true" />
      <span className="memory-card-text">
        {t(MEMORY_SUMMARY_KEYS[memory.status], { content: memory.content })}
      </span>
      {state === 'undone' ? (
        <span className="draft-note ok"><CheckIcon /> {t('chat.memoryUndone')}</span>
      ) : (
        <button
          type="button"
          className="memory-card-undo"
          onClick={undo}
          disabled={state === 'undoing'}
        >
          {state === 'undoing' ? t('chat.memoryUndoing') : t('chat.memoryUndo')}
        </button>
      )}
      <button type="button" className="memory-card-manage" onClick={onManage}>
        {t('chat.memoryManage')}
      </button>
      {state === 'error' && (
        <p className="draft-note error memory-card-error" role="alert">{t('chat.memoryUndoFailed')}</p>
      )}
    </div>
  )
}

function ToolCallBlock({ tool, onManualToggle }) {
  const { t } = useLanguage()
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
            <StreamingDots label={t('tool.isRunning', { name: tool.display || tool.name })} />
            {t('tool.running')}
          </span>
        ) : failed ? (
          <span className="tool-status failed">{t('tool.failed')}</span>
        ) : (
          <span className="tool-status done">
            <CheckIcon />
            {t('tool.done')}
          </span>
        )}
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <div className="tool-body">
          {argsText && (
            <div className="tool-section">
              <div className="tool-section-label">{t('tool.input')}</div>
              <pre
                className="tool-args"
                dangerouslySetInnerHTML={{ __html: highlightJson(argsText) }}
              />
            </div>
          )}
          <div className="tool-section">
            <div className="tool-section-label">{t('tool.result')}</div>
            <div className="tool-result">
              {imageResultSummary(tool, t) ??
                webpageResultSummary(tool, t) ??
                <EmojiText text={tool.result ?? t('tool.runningEllipsis')} />}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ThinkingBlock({ parts: segmentParts, streaming, fallbackSeconds = null }) {
  const { t } = useLanguage()
  // Draft, deletion and memory-change calls are surfaced as their own cards
  // below the message, so hide the raw tool blocks to avoid showing them twice.
  const parts = segmentParts.filter(
    (p) => !(p.type === 'tool' && (draftFromTool(p) || deleteFromTool(p) || memoryFromTool(p))),
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
    ? t('chat.thinking')
    : seconds != null
      ? formatThought(seconds, t)
      : t('chat.thoughtPlain')

  // Auto-expand while thinking runs, auto-close once it's done. Any manual
  // click on the block head or on a tool call inside it takes over for good.
  const [manualOpen, setManualOpen] = useState(null)
  const open = manualOpen ?? !done

  if (parts.length === 0) return null

  return (
    <div className="thinking">
      <button
        type="button"
        className="thinking-head"
        onClick={() => setManualOpen(!open)}
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
                onManualToggle={() => setManualOpen(open)}
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
  const { t } = useLanguage()
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
      aria-label={copied ? t('chat.copiedLabel') : t('chat.copy')}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      <span aria-live="polite">{copied ? t('chat.copied') : t('chat.copy')}</span>
    </button>
  )
}

function RetryMessageButton({ onRetry }) {
  const { t } = useLanguage()
  return (
    <button
      type="button"
      className="copy-msg"
      onClick={onRetry}
      aria-label={t('chat.retry')}
    >
      <RetryIcon />
      <span>{t('chat.retry')}</span>
    </button>
  )
}

function RegenerateMessageButton({ onRegenerate }) {
  const { t } = useLanguage()
  return (
    <button
      type="button"
      className="copy-msg"
      onClick={onRegenerate}
      aria-label={t('chat.regenerate')}
    >
      <RetryIcon />
      <span>{t('chat.regenerate')}</span>
    </button>
  )
}

// Arrows + "x/y" version switcher, shown only when a message has branches.
function BranchNav({ index, total, onPrev, onNext }) {
  const { t } = useLanguage()
  return (
    <div className="branch-nav" role="group" aria-label={t('chat.versionOf', { n: index + 1, total })}>
      <button
        type="button"
        className="branch-arrow prev"
        onClick={onPrev}
        disabled={index <= 0}
        aria-label={t('chat.prevVersion')}
      >
        <ChevronIcon />
      </button>
      <span className="branch-count" aria-live="polite">{index + 1}/{total}</span>
      <button
        type="button"
        className="branch-arrow next"
        onClick={onNext}
        disabled={index >= total - 1}
        aria-label={t('chat.nextVersion')}
      >
        <ChevronIcon />
      </button>
    </div>
  )
}

function EditMessageForm({ value, onChange, onSend, onCancel }) {
  const { t } = useLanguage()
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
          {t('common.cancel')}
        </button>
        <button
          type="button"
          className="edit-msg-send"
          onClick={onSend}
          disabled={!value.trim()}
        >
          {t('chat.send')}
        </button>
      </div>
    </div>
  )
}

function MessageRow({ message, prev, isActiveStream, user, onRetry, onDraftSent, onEventDeleted, onMemoryUndone, onManageMemory, onOpenWebpage, branch, onEdit, onRegenerate, onNavigateBranch, storyMode = false, showChoices = false, onPickChoice }) {
  const { t } = useLanguage()
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
  // Old draft and deletion cards can't act while their integration is off.
  const drafts = user?.gmailEnabled === true ? toolParts.map(draftFromTool).filter(Boolean) : []
  const proposals =
    user?.calendarEnabled === true ? toolParts.map(deleteFromTool).filter(Boolean) : []
  const memories = toolParts.map(memoryFromTool).filter(Boolean)
  const images = toolParts.map(imageFromPart).filter(Boolean)
  const webpages = webpagesFromParts(toolParts)
  const choices = showChoices ? toolParts.map(choicesFromPart).filter(Boolean).at(-1) : null
  // Story mode marks out-of-character turns so they read apart from the story.
  const ooc = storyMode && (m.role === 'user' ? isOoc(m.content) : isOocReply(m.content))
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
    <div className={`msg-row ${m.role}${startsRun ? ' starts' : ''}${ooc ? ' ooc' : ''}`}>
      {m.role === 'assistant' ? (
        <div className="msg-avatar">
          <img src="/plates/msg-avatar.webp" alt="" />
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
              <div key={`text-${si}`} className="bubble" dir="auto">
                <Markdown text={seg.text} />
              </div>
            ),
          )}
          {awaitingReply && segments.length === 0 && (
            <div className="bubble bubble-waiting">
              <StreamingDots />
            </div>
          )}
          {choices && !isActiveStream && !m.failed && (
            <StoryChoices options={choices} onPick={onPickChoice} />
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
          {webpages.map((w) => (
            <WebpageCard key={w.webpageId} webpage={w} onOpen={() => onOpenWebpage?.(w)} />
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
          {!isActiveStream && memories.map((mem, mi) => (
            <MemoryCard
              key={`${mem.tool}-${mem.id}-${mi}`}
              memory={mem}
              onUndone={onMemoryUndone}
              onManage={onManageMemory}
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
            <div className="bubble" dir="auto">
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
                aria-label={t('chat.editMessage')}
              >
                <PencilIcon />
                <span>{t('chat.edit')}</span>
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
  modes,
  onMode,
  effort,
  onEffort,
  inputRef,
}) {
  const { t } = useLanguage()
  const composerRef = useRef(null)
  // Story mode: the next message goes out of character as "(ooc: …)".
  const [ooc, setOoc] = useState(false)
  const storyMode = mode === 'story'
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

  const autosize = useCallback(() => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [])

  useEffect(() => {
    autosize()
  }, [autosize, draft, voice.interim])

  // The measurement depends on width and fonts, which can settle after the
  // first render (web fonts loading, a language/direction switch), so re-measure.
  useEffect(() => {
    const el = composerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(autosize)
    observer.observe(el.parentElement ?? el)
    document.fonts?.ready.then(autosize)
    return () => observer.disconnect()
  }, [autosize])

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
    onSend(storyMode && ooc ? { ooc: true } : undefined)
    setOoc(false)
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

  const hasContent = Boolean(draft.trim()) || attachments.length > 0
  const showMic = voice.supported && (voice.listening || !hasContent)

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
            {uploading && <span className="attach-uploading">{t('chat.uploading')}</span>}
          </div>
        )}
        {attachError && <p className="attach-error" role="alert">{attachError}</p>}
        {voice.error && <p className="attach-error" role="alert">{voice.error}</p>}
        <div className={`composer-box${voice.listening ? ' listening' : ''}`}>
          <div className="input">
            <textarea
              rows={1}
              placeholder={
                storyMode
                  ? (ooc ? t('story.placeholderOoc') : t('story.placeholder'))
                  : mode === 'tutor' ? t('chat.placeholderTutor') : t('chat.placeholder')
              }
              aria-label={t('chat.messageLabel')}
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
          <div className="composer-tools">
            <button
              type="button"
              className="attach has-tip tip-up tip-start"
              data-tip={t('chat.attach')}
              aria-label={t('chat.attach')}
              onClick={() => fileRef.current?.click()}
              disabled={isStreaming || uploading}
            >
              <PaperclipIcon />
            </button>
            <ModeSwitch mode={mode} modes={modes} onMode={onMode} disabled={isStreaming} />
            {storyMode && (
              <button
                type="button"
                className={`effort-pill story-ooc${ooc ? ' on' : ''} has-tip tip-up`}
                data-tip={t('story.oocTip')}
                aria-pressed={ooc}
                onClick={() => setOoc((o) => !o)}
                disabled={isStreaming}
              >
                {t('story.ooc')}
              </button>
            )}
            <EffortSelector effort={effort} onChange={onEffort} disabled={isStreaming} />
            {/* Mic and send share one spot: the mic while there is nothing to
                send (or while dictating), send once there is. */}
            {showMic ? (
              <button
                type="button"
                className={`mic has-tip tip-up${voice.listening ? ' rec' : ''}`}
                data-tip={voice.listening ? t('chat.listening') : t('chat.speak')}
                aria-label={voice.listening ? t('chat.stopVoice') : t('chat.speak')}
                aria-pressed={voice.listening}
                onClick={voice.toggle}
                disabled={isStreaming || uploading}
              >
                <MicIcon />
              </button>
            ) : (
              <button
                className="send"
                type="submit"
                aria-label={t('chat.sendMessage')}
                disabled={!hasContent || isStreaming || uploading}
              >
                <SendIcon />
              </button>
            )}
          </div>
        </div>
        <p className="composer-hint">{t('chat.aiHint')}</p>
      </form>
  )
}

export function ChatWindow({ chat, user, isStreaming, draft, onDraft, onSend, onRetry, onEdit, onRegenerate, onNavigateBranch, onDraftSent, onEventDeleted, onMemoryUndone, onManageMemory, onManageCharacters, onOpenWebpage, inputRef, effort, onEffort, mode, modes, onMode, onSendText, onStory, attachments = [], uploading = false, attachError = '', onAttach, onRemoveAttachment }) {
  const { t } = useLanguage()
  const listRef = useRef(null)
  const storyMode = mode === 'story'
  const lastIndex = chat.messages.length - 1

  const canAttach = !isStreaming && !uploading

  // Window-level drag & drop. dragenter/dragleave fire for every child element
  // crossed, so a depth counter keeps the overlay visible until we really leave.
  // A dragover watchdog is the safety net: touch browsers often never fire
  // dragleave/drop when a drag is canceled, which otherwise strands the overlay.
  const dragDepth = useRef(0)
  const [dragActive, setDragActive] = useState(false)

  useEffect(() => {
    // Dragging an <img> on the page also carries a file payload, but only drags
    // that start in this document fire dragstart, so use it to ignore them.
    let internalDrag = false
    const onDragStart = () => { internalDrag = true }
    const onDragEnd = () => { internalDrag = false }
    const hasFiles = (e) => !internalDrag && Array.from(e.dataTransfer?.types || []).includes('Files')
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
    window.addEventListener('dragstart', onDragStart)
    window.addEventListener('dragenter', onDragEnter)
    window.addEventListener('dragover', onDragOver)
    window.addEventListener('dragleave', onDragLeave)
    window.addEventListener('drop', onDrop)
    window.addEventListener('drop', onDragEnd)
    window.addEventListener('dragend', onDragEnd)
    window.addEventListener('dragend', hide)
    window.addEventListener('blur', hide)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      if (hideTimer) clearTimeout(hideTimer)
      window.removeEventListener('dragstart', onDragStart)
      window.removeEventListener('dragenter', onDragEnter)
      window.removeEventListener('dragover', onDragOver)
      window.removeEventListener('dragleave', onDragLeave)
      window.removeEventListener('drop', onDragEnd)
      window.removeEventListener('drop', onDrop)
      window.removeEventListener('dragend', onDragEnd)
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
      <div className="message-list" ref={listRef} aria-label={t('chat.conversation', { title: chat.title })}>
        {storyMode && chat.messages.length > 0 && (
          <StoryBar chat={chat} onStory={onStory} onManageCharacters={onManageCharacters} disabled={isStreaming} />
        )}
        {storyMode && chat.messages.length === 0 && !isStreaming && (
          <StoryStart onStart={onSendText} onManageCharacters={onManageCharacters} disabled={isStreaming} />
        )}
        {!storyMode && chat.messages.length === 0 && !isStreaming && (
          <div className="welcome" role="status">
            <img className="welcome-hero" src="/plates/welcome-hero.webp" alt={t('chat.welcomeAlt')} />
            <h1 className="welcome-title">{t('landing.title')}</h1>
            {mode === 'tutor' ? (
              <p className="welcome-sub">{t('chat.welcomeTutor')}</p>
            ) : (
              <p className="welcome-sub">{t('landing.sub')}</p>
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
            onMemoryUndone={onMemoryUndone}
            onManageMemory={onManageMemory}
            onOpenWebpage={onOpenWebpage}
            storyMode={storyMode}
            showChoices={storyMode && i === lastIndex && !isStreaming}
            onPickChoice={(text) => onSendText?.(text)}
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
        modes={modes}
        onMode={onMode}
        effort={effort}
        onEffort={onEffort}
        inputRef={inputRef}
      />

      {dragActive && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-overlay-card">
            <PaperclipIcon />
            <span>{t('chat.drop')}</span>
          </div>
        </div>
      )}
    </>
  )
}
