import { useEffect, useRef, useState } from 'react'
import { EmojiText } from '../lib/emoji'

const CHAR_MS = 40

export function TypewriterTitle({ text, onDone }) {
  const [count, setCount] = useState(0)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    setCount(0)
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
      setCount(text.length)
      done.current?.()
      return
    }
    let i = 0
    const timer = setInterval(() => {
      i += 1
      setCount(i)
      if (i >= text.length) {
        clearInterval(timer)
        done.current?.()
      }
    }, CHAR_MS)
    return () => clearInterval(timer)
  }, [text])

  return (
    <span className="chat-title typewriter" aria-label={text}>
      <span aria-hidden="true">
        <EmojiText text={text.slice(0, count)} />
      </span>
      {count < text.length && <span className="typewriter-caret" aria-hidden="true" />}
    </span>
  )
}
