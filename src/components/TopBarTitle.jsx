import { useEffect, useRef, useState } from 'react'
import { TypewriterTitle } from './TypewriterTitle'
import { useLanguage } from '../i18n/LanguageProvider'

export function TopBarTitle({ title, typing, onTyped, onRename }) {
  const { t } = useLanguage()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(title)
  const skip = useRef(false)

  useEffect(() => {
    if (!editing) setDraft(title)
  }, [title, editing])

  if (!title) return null

  const start = () => {
    skip.current = false
    setDraft(title)
    setEditing(true)
  }

  const commit = () => {
    if (skip.current) {
      skip.current = false
      return
    }
    const next = draft.trim()
    setEditing(false)
    if (next && next !== title) onRename(next)
  }

  if (editing) {
    return (
      <span className="chat-title-edit">
        <input
          className="chat-title-input"
          value={draft}
          autoFocus
          aria-label={t('sidebar.renameChat')}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={(e) => {
            const end = e.target.value.length
            e.target.setSelectionRange(end, end)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              skip.current = true
              setEditing(false)
            }
          }}
          onBlur={commit}
        />
        <span className="chat-title-mirror" aria-hidden="true">
          {draft || ' '}
        </span>
      </span>
    )
  }

  return (
    <button type="button" className="chat-title-btn" onClick={start}>
      {typing ? (
        <TypewriterTitle text={title} onDone={onTyped} className="typewriter" />
      ) : (
        title
      )}
    </button>
  )
}
