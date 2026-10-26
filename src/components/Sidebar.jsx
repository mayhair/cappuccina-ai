import { useEffect, useRef, useState } from 'react'
import { ChatIcon, TrashIcon, PlusIcon, SearchIcon, CloseIcon, DotsIcon, PencilIcon, LogoutIcon, GearIcon, StarIcon } from './Icons'
import { TypewriterTitle } from './TypewriterTitle'
import { ChatTitle } from './ChatTitle'
import { EmojiText } from '../lib/emoji'
import { useLanguage } from '../i18n/LanguageProvider'

export function Sidebar({ chats, activeChatId, onSelect, onNewChat, onDelete, onRename, query, onQuery, searchInputRef, collapsed, typingTitleId, onTitleTyped, user, onLogout, onSettings, onClose }) {
  const { t, tr } = useLanguage()
  const [pendingDelete, setPendingDelete] = useState(null)
  const [menuFor, setMenuFor] = useState(null)
  const [menuUp, setMenuUp] = useState(false)
  const [renaming, setRenaming] = useState(null)
  const [draftTitle, setDraftTitle] = useState('')
  const [accountMenu, setAccountMenu] = useState(false)
  const userBarRef = useRef(null)
  const listRef = useRef(null)
  const isTouch = useState(
    () => window.matchMedia?.('(pointer: coarse)').matches ?? false,
  )[0]

  useEffect(() => {
    if (!accountMenu) return
    const close = (e) => {
      if (userBarRef.current?.contains(e.target)) return
      setAccountMenu(false)
    }
    const esc = (e) => {
      if (e.key === 'Escape') setAccountMenu(false)
    }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [accountMenu])

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

  // The untitled placeholder is stored in English; show it in the UI language.
  const shownTitle = (title) => (title === 'New chat' ? t('sidebar.newChat') : title)

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

  const MENU_HEIGHT = 96

  const openMenu = (event, id) => {
    if (menuFor === id) {
      setMenuFor(null)
      return
    }
    const list = listRef.current
    const item = event.currentTarget.closest('.chat-list-item')
    if (list && item) {
      const listRect = list.getBoundingClientRect()
      const itemRect = item.getBoundingClientRect()
      const below = listRect.bottom - itemRect.bottom
      const above = itemRect.top - listRect.top
      setMenuUp(below < MENU_HEIGHT && (above >= MENU_HEIGHT || above > below))
    } else {
      setMenuUp(false)
    }
    setMenuFor(id)
  }

  return (
    <>
    <aside
      className={`sidebar${collapsed ? ' is-hidden' : ''}`}
      inert={collapsed ? '' : undefined}
      aria-hidden={collapsed || undefined}
    >
      <div className="side-head">
        <div className="side-avatar">
          <img src="/plates/msg-avatar.webp" alt={t('sidebar.avatarAlt')} />
        </div>
        <div className="side-name">{t('sidebar.brand')}</div>
        <button
          type="button"
          className="sidebar-close"
          aria-label={t('sidebar.close')}
          onClick={onClose}
        >
          <CloseIcon />
        </button>
      </div>

      <button className="new-chat-btn" type="button" onClick={onNewChat}>
        <PlusIcon />
        {t('sidebar.newChat')}
      </button>

      <label className="search">
        <SearchIcon />
        <input
          ref={searchInputRef}
          type="search"
          placeholder={t('sidebar.searchPlaceholder')}
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label={t('sidebar.search')}
        />
        {query && (
          <button
            type="button"
            className="search-clear"
            aria-label={t('sidebar.clearSearch')}
            onClick={() => {
              onQuery('')
              searchInputRef.current?.focus()
            }}
          >
            <CloseIcon />
          </button>
        )}
      </label>

      <nav className="chat-list" aria-label={t('sidebar.history')} ref={listRef}>
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
                  aria-label={t('sidebar.renameChat')}
                />
              ) : c.id === typingTitleId ? (
                <TypewriterTitle text={shownTitle(c.title)} onDone={onTitleTyped} />
              ) : (
                <ChatTitle text={shownTitle(c.title)} />
              )}
              {c.tag && (
                <span className={`chat-tag${c.tag === 'TUTOR' ? ' tutor' : ''}`}>{c.tag === 'TUTOR' ? t('mode.tutor') : c.tag}</span>
              )}
              {!editing && (
                <button
                  type="button"
                  className="chat-menu-btn"
                  aria-label={t('sidebar.chatOptions', { title: c.title })}
                  aria-haspopup="menu"
                  aria-expanded={menuFor === c.id}
                  onClick={(e) => {
                    e.stopPropagation()
                    openMenu(e, c.id)
                  }}
                >
                  <DotsIcon />
                </button>
              )}
              {menuFor === c.id && (
                <div
                  className={`chat-menu${menuUp ? ' up' : ''}`}
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
                    {t('sidebar.rename')}
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
                    {t('sidebar.delete')}
                  </button>
                </div>
              )}
            </div>
          )
        })}
      </nav>

      <div
        className="user-bar"
        ref={userBarRef}
        onMouseEnter={isTouch ? undefined : () => setAccountMenu(true)}
        onMouseLeave={isTouch ? undefined : () => setAccountMenu(false)}
      >
        <div className="user-info">
          {user.avatarUrl ? (
            <img className="user-avatar" src={user.avatarUrl} alt="" aria-hidden="true" referrerPolicy="no-referrer" />
          ) : (
            <span className="user-avatar" aria-hidden="true">
              {(user.displayName || '?').slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="user-details">
            <span className="user-name">
              <EmojiText text={user.displayName} />
            </span>
            <span className="user-email">{user.email}</span>
          </span>
        </div>
        <div className="user-hint-wrap">
          <button
            type="button"
            className="user-hint"
            aria-haspopup="menu"
            aria-expanded={accountMenu}
            aria-label={t('sidebar.accountOptions')}
            onFocus={() => {
              if (!isTouch) setAccountMenu(true)
            }}
            onBlur={(e) => {
              if (!userBarRef.current?.contains(e.relatedTarget)) setAccountMenu(false)
            }}
            onClick={() => {
              if (isTouch) setAccountMenu((open) => !open)
            }}
          >
            {isTouch ? t('sidebar.tapForOptions') : t('sidebar.hoverForOptions')}
          </button>
          {accountMenu && (
            <div className="user-menu" role="menu">
              <button
                type="button"
                role="menuitem"
                className="user-menu-item"
                onClick={() => {
                  setAccountMenu(false)
                  onSettings()
                }}
              >
                <GearIcon />
                {t('settings.title')}
              </button>
              {/* Backstage is its own page (backstage/index.html), so a real link. */}
              <a
                href="/backstage/"
                role="menuitem"
                className="user-menu-item"
                onClick={() => setAccountMenu(false)}
              >
                <StarIcon />
                {t('sidebar.backstage')}
              </a>
              <button
                type="button"
                role="menuitem"
                className="user-menu-item danger"
                onClick={() => {
                  setAccountMenu(false)
                  onLogout()
                }}
              >
                <LogoutIcon />
                {t('sidebar.logout')}
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>

      {target && (
        <div className="modal-veil" role="dialog" aria-modal="true" aria-labelledby="delete-title" onMouseDown={(e) => e.target === e.currentTarget && setPendingDelete(null)}>
          <div className="modal">
            <button
              type="button"
              className="modal-x"
              aria-label={t('common.close')}
              onClick={() => setPendingDelete(null)}
            >
              ✕
            </button>
            <div className="modal-avatar">
              <img src="/plates/msg-avatar.webp" alt="" />
            </div>
            <h2 id="delete-title">
              <EmojiText text={t('sidebar.deleteModal.title')} />
            </h2>
            <p>
              {tr('sidebar.deleteModal.body', { title: target.title })} <EmojiText text="💔" />
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-keep" onClick={() => setPendingDelete(null)}>
                {t('sidebar.deleteModal.keep')}
              </button>
              <button
                type="button"
                className="btn-toss"
                onClick={() => {
                  onDelete(target.id)
                  setPendingDelete(null)
                }}
              >
                {t('sidebar.deleteModal.toss')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
