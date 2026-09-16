import { useEffect, useState } from 'react'
import { ChatIcon, TrashIcon, PlusIcon, SearchIcon, DotsIcon, PencilIcon } from './Icons'
import { TypewriterTitle } from './TypewriterTitle'
import { EmojiText } from '../lib/emoji'

export function Sidebar({ chats, activeChatId, onSelect, onNewChat, onDelete, onRename, query, onQuery, typingTitleId, onTitleTyped }) {
  const [pendingDelete, setPendingDelete] = useState(null)
  const [menuFor, setMenuFor] = useState(null)
  const [renaming, setRenaming] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')

  useEffect(() => {
    if (!pendingDelete) return
    const close = (e) => {
      if (e.key === 'Escape') setPendingDelete(null)
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [pendingDelete])

  useEffect(() => {
    if (!menuFor) return
    const close = (e) => {
      if (e.target.closest?.('.chat-menu') || e.target.closest?.('.chat-menu-btn')) return
      setMenuFor(null)
    }
    window.addEventListener('mousedown', close)
    return () => window.removeEventListener('mousedown', close)
  }, [menuFor])

  const target = chats.find((c) => c.id === pendingDelete)

  const startRename = (c) => {
    setMenuFor(null)
    setRenaming(c.id)
    setDraftTitle(c.title)
  }

  const commitRename = (id) => {
    onRename(id, draftTitle)
    setRenaming(null)
  }

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
        {chats.map((c) => {
          const editing = renaming === c.id
          return (
            <div
              key={c.id}
              role="button"
              tabIndex={0}
              className={`chat-list-item${c.id === activeChatId ? ' active' : ''}`}
              onClick={() => !editing && onSelect(c.id)}
              onKeyDown={(e) => {
                if (editing || e.target !== e.currentTarget) return
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(c.id)
                }
              }}
            >
              <ChatIcon />
              {editing ? (
                <input
                  className="chat-rename-input"
                  value={draftTitle}
                  autoFocus
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitRename(c.id)
                    if (e.key === 'Escape') setRenaming(null)
                  }}
                  onBlur={() => commitRename(c.id)}
                  aria-label="Rename chat"
                />
              ) : c.id === typingTitleId ? (
                <TypewriterTitle text={c.title} onDone={onTitleTyped} />
              ) : (
                <span className="chat-title">
                  <EmojiText text={c.title} />
                </span>
              )}
              {c.tag && (
                <span className={`tag${c.tag === 'TUTOR' ? ' tutor' : ''}`}>{c.tag}</span>
              )}
              {!editing && (
                <button
                  type="button"
                  className="chat-menu-btn"
                  aria-label={`Options for ${c.title}`}
                  aria-haspopup="menu"
                  aria-expanded={menuFor === c.id}
                  onClick={(e) => {
                    e.stopPropagation()
                    setMenuFor(menuFor === c.id ? null : c.id)
                  }}
                >
                  <DotsIcon />
                </button>
              )}
              {menuFor === c.id && (
                <div
                  className="chat-menu"
                  role="menu"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="chat-menu-item"
                    onClick={() => startRename(c)}
                  >
                    <PencilIcon />
                    Rename
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    className="chat-menu-item danger"
                    onClick={() => {
                      setMenuFor(null)
                      setPendingDelete(c.id)
                    }}
                  >
                    <TrashIcon />
                    Delete
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </nav>

      <p className="side-note">
        <EmojiText text="Cappuccina is an AI character — not a person, but she loves to help. 💕" />
      </p>
    </aside>

      {target && (
        <div className="modal-veil" role="dialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(e) => e.target === e.currentTarget && setPendingDelete(null)}>
          <div className="modal">
            <div className="modal-avatar">
              <img src="/plates/msg-avatar.png" alt="" />
            </div>
            <h2 id="delete-title">
              <EmojiText text="Sip—one moment! ☕" />
            </h2>
            <p>
              Are you <i>sure</i> you want to pour <strong>“{target.title}”</strong> down the drain? That chat will be gone <em>forever</em> — I can't get it back. <EmojiText text="💔" />
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
