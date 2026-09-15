import { useEffect, useRef } from 'react'
import { PersonIcon, SendIcon } from './Icons'

export function ChatWindow({ chat, isTyping, isStreaming, draft, onDraft, onSend, inputRef }) {
  const listRef = useRef(null)

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [isTyping, chat.messages])

  return (
    <>
      <div className="message-list" ref={listRef} aria-label={`Conversation: ${chat.title}`}>
        {chat.messages.length === 0 && !isTyping && (
          <div className="welcome" role="status">
            <img className="welcome-hero" src="/plates/welcome-hero.png" alt="Ballerina Cappuccina waving hello" />
            <h1 className="welcome-title">Hi! I&apos;m Ballerina Cappuccina</h1>
            <p className="welcome-sub">Your AI chat friend — ask me anything, or let&apos;s learn together</p>
          </div>
        )}
        {chat.messages.map((m, i) => {
          const prev = chat.messages[i - 1]
          const startsRun = !prev || prev.role !== m.role
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
              <div className="bubble">
                <p>{m.content || '…'}</p>
              </div>
            </div>
          )
        })}
        {isTyping && (
          <div className="msg-row assistant starts">
            <div className="msg-avatar">
              <img src="/plates/msg-avatar.png" alt="" />
            </div>
            <div className="typing-pill" aria-label="Cappuccina is typing">
              <span className="streaming" aria-hidden="true">
                <i /><i /><i />
              </span>
            </div>
          </div>
        )}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          onSend()
        }}
      >
        <div className="input">
          <input
            type="text"
            placeholder="Ask me anything..."
            aria-label="Message Cappuccina"
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            ref={inputRef}
          />
        </div>
        <button className="send" type="submit" aria-label="Send message" disabled={!draft.trim() || isTyping || isStreaming}>
          <SendIcon />
        </button>
      </form>
    </>
  )
}
