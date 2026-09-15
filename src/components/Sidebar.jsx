import { useEffect, useState } from 'react'
import { ChatIcon, TrashIcon, PlusIcon, SearchIcon } from './Icons'

export function Sidebar({ chats, activeChatId, onSelect, onNewChat, onDelete, query, onQuery }) {
  const [pendingDelete, setPendingDelete] = useState(null)

  useEffect(() => {
    if (!pendingDelete) return
    const close = (e) => {
      if (e.key === 'Escape') setPendingDelete(null)
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [pendingDelete])

  const target = chats.find((c) => c.id === pendingDelete)

  return (
    <>
    <aside className="sidebar">
      <div className="side-head">
        <div className="side-avatar">
          <img src="/plates/msg-avatar.png" alt="Ballerina Cappuccina, an AI character whose head is a coffee cup" />
        </div>
        <div className="side-name">Cappuccina</div>
      </div>

      <button className="new-chat-btn" type="button" onClick={onNewChat}>
        <PlusIcon />
        New chat
      </button>

      <label className="search">
        <SearchIcon />
        <input
          type="search"
          placeholder="Search chats..."
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label="Search chats"
        />
      </label>

      <nav className="chat-list" aria-label="Chat history">
        {chats.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`chat-list-item${c.id === activeChatId ? ' active' : ''}`}
            onClick={() => onSelect(c.id)}
          >
            <ChatIcon />
            <span>{c.title}</span>
            {c.tag && <span className="tag">{c.tag}</span>}
            <TrashIcon
              className="trash"
              onClick={(e) => {
                e.stopPropagation()
                setPendingDelete(c.id)
              }}
              aria-label={`Delete chat: ${c.title}`}
              title="Delete chat"
            />
          </button>
        ))}
      </nav>

      <p className="side-note">Cappuccina is an AI character — not a person, but she loves to help. 💕</p>
    </aside>

      {target && (
        <div className="modal-veil" role="dialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(e) => e.target === e.currentTarget && setPendingDelete(null)}>
          <div className="modal">
            <div className="modal-avatar">
              <img src="/plates/msg-avatar.png" alt="" />
            </div>
            <h2 id="delete-title">Sip—one moment! ☕</h2>
            <p>
              Are you <i>sure</i> you want to pour <strong>“{target.title}”</strong> down the drain? That chat will be gone <em>forever</em> — I can't get it back. 💔
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-keep" onClick={() => setPendingDelete(null)}>
                Keep it, safe!
              </button>
              <button
                type="button"
                className="btn-toss"
                onClick={() => {
                  onDelete(target.id)
                  setPendingDelete(null)
                }}
              >
                Toss it, arrivederci!
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
