import { useEffect, useLayoutEffect, useRef, useState } from 'react'

export function TopBarTitle({ title, onRename }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(title)
  const inputRef = useRef(null)
  const mirrorRef = useRef(null)
  const skip = useRef(false)

  useEffect(() => {
    if (!editing) setDraft(title)
  }, [title, editing])

  useLayoutEffect(() => {
    if (!editing) return
    const input = inputRef.current
    const mirror = mirrorRef.current
    if (!input || !mirror) return
    input.style.width = `${mirror.offsetWidth}px`
  }, [editing, draft])

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
          ref={inputRef}
          className="chat-title-input"
          value={draft}
          autoFocus
          aria-label="Rename chat"
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
        <span ref={mirrorRef} className="chat-title-mirror" aria-hidden="true">
          {draft || ' '}
        </span>
      </span>
    )
  }

  return (
    <button type="button" className="chat-title-btn" onClick={start}>
      {title}
    </button>
  )
}
