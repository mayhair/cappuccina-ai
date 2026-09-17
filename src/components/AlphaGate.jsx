import { useEffect, useState } from 'react'

const ACK_KEY = 'cappuccina-alpha-ack'
const ACK_VERSION = 'v1'

export function AlphaGate() {
  const [open, setOpen] = useState(
    () => localStorage.getItem(ACK_KEY) !== ACK_VERSION,
  )

  useEffect(() => {
    if (!open) return
    const esc = (e) => {
      if (e.key === 'Escape') acknowledge()
    }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [open])

  function acknowledge() {
    localStorage.setItem(ACK_KEY, ACK_VERSION)
    setOpen(false)
  }

  if (!open) return null

  return (
    <div
      className="modal-veil"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alpha-gate-title"
      onMouseDown={(e) => e.target === e.currentTarget && acknowledge()}
    >
      <div className="modal alpha-gate">
        <span className="alpha-gate-tag">Early alpha</span>
        <h2 className="alpha-gate-title" id="alpha-gate-title">
          I'm still in early development
        </h2>
        <p className="alpha-gate-body">
          Things might change or break sometimes while I'm being built. I'm an AI,
          not a person, so please always double-check important stuff.
        </p>
        <p className="alpha-gate-age">
          Right now I'm only for ages <strong>13 and up</strong>. My safety checks are
          still being tested in alpha, and I can say surprising or wrong things, so younger
          kids should wait until I'm ready.
        </p>
        <button type="button" className="landing-cta alpha-gate-button" onClick={acknowledge} autoFocus>
          Got it
        </button>
      </div>
    </div>
  )
}
