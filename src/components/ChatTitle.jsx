import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { EmojiText } from '../lib/emoji'

const GAP = 9
const EDGE = 8

function placeBubble(anchor, bubble) {
  const a = anchor.getBoundingClientRect()
  const b = bubble.getBoundingClientRect()
  const centered = a.left + a.width / 2 - b.width / 2
  const left = Math.max(EDGE, Math.min(window.innerWidth - b.width - EDGE, centered))
  const below = window.innerHeight - a.bottom >= b.height + GAP
  const top = below ? a.bottom + GAP : a.top - b.height - GAP
  return { left, top }
}

export function ChatTitle({ text }) {
  const anchorRef = useRef(null)
  const bubbleRef = useRef(null)
  const [truncated, setTruncated] = useState(false)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState(null)

  useLayoutEffect(() => {
    const el = anchorRef.current
    if (!el) return
    const measure = () => setTruncated(el.scrollWidth > el.clientWidth + 1)
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [text])

  useLayoutEffect(() => {
    if (!open) return
    const anchor = anchorRef.current
    const bubble = bubbleRef.current
    if (!anchor || !bubble) return
    const update = () => setPos(placeBubble(anchor, bubble))
    update()
    window.addEventListener('scroll', update, true)
    window.addEventListener('resize', update)
    return () => {
      window.removeEventListener('scroll', update, true)
      window.removeEventListener('resize', update)
    }
  }, [open, text])

  const show = useCallback(() => {
    if (truncated) setOpen(true)
  }, [truncated])

  const hide = useCallback(() => {
    setOpen(false)
    setPos(null)
  }, [])

  return (
    <span
      className="chat-title"
      ref={anchorRef}
      onMouseEnter={show}
      onMouseLeave={hide}
    >
      <EmojiText text={text} />
      {open &&
        createPortal(
          <span
            ref={bubbleRef}
            aria-hidden="true"
            className={`tip-bubble${pos ? ' in' : ''}`}
            style={pos || { left: 0, top: 0, visibility: 'hidden' }}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  )
}
