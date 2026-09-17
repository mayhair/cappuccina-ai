import { useEffect, useRef, useState } from 'react'
import { PersonIcon, SendIcon, ChevronIcon, WrenchIcon, CopyIcon, CheckIcon, RetryIcon } from './Icons'
import { EffortSelector } from './EffortSelector'
import { ModeSwitch } from './ModeSwitch'
import { Markdown } from '../lib/markdown'
import { EmojiText } from '../lib/emoji'

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

function ThinkingBlock({ message }) {
  const [open, setOpen] = useState(false)
  const parts = thinkingParts(message)
  if (parts.length === 0) return null

  const done = message.thinkingDone && message.thoughtSeconds != null
  const label = done ? formatThought(message.thoughtSeconds) : 'Thinking...'

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

export function ChatWindow({ chat, isStreaming, draft, onDraft, onSend, onRetry, inputRef, effort, onEffort, mode, subject, onMode }) {
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
                  <ThinkingBlock message={m} />
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
