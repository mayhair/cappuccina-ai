import { useState } from 'react'
import { EFFORTS } from '../hooks/useChatController'
import { ChevronIcon } from './Icons'

export function EffortSelector({ effort, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const index = Math.max(0, EFFORTS.findIndex((e) => e.label === effort))
  const percent = (index / (EFFORTS.length - 1)) * 100

  const pick = (label) => {
    onChange(label)
  }

  return (
    <div className="effort">
      <button
        type="button"
        className="effort-pill"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`Reasoning effort: ${effort}. Change effort`}
        disabled={disabled}
      >
        <span>{effort}</span>
        <ChevronIcon className={`chev${open ? ' open' : ''}`} />
      </button>
      {open && (
        <>
          <button
            type="button"
            className="effort-veil"
            aria-hidden="true"
            tabIndex={-1}
            onClick={() => setOpen(false)}
          />
          <div className="effort-pop" role="dialog" aria-label="Select reasoning effort">
            <div className="effort-name">
              <span>{effort}</span>
            </div>
            <div className="effort-sub">
              <span>Reasoning effort</span>
              <button
                type="button"
                className="effort-help"
                onClick={() => setHelpOpen(true)}
              >
                (What is this?)
              </button>
            </div>
            <input
              type="range"
              className="effort-range"
              min={0}
              max={EFFORTS.length - 1}
              step={1}
              value={index}
              onChange={(e) => pick(EFFORTS[Number(e.target.value)].label)}
              aria-label="Reasoning effort"
              style={{ '--fill': `${percent}%` }}
            />
            <div className="effort-ticks" aria-hidden="true">
              {EFFORTS.map((e) => (
                <span key={e.label} className={e.label === effort ? 'on' : ''} />
              ))}
            </div>
            <div className="effort-levels">
              {EFFORTS.map((e) => (
                <button
                  key={e.label}
                  type="button"
                  className={e.label === effort ? 'on' : ''}
                  onClick={() => pick(e.label)}
                >
                  {e.label}
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {helpOpen && (
        <div
          className="modal-veil"
          role="dialog"
          aria-modal="true"
          aria-labelledby="effort-help-title"
          onMouseDown={(e) => e.target === e.currentTarget && setHelpOpen(false)}
        >
          <div className="modal">
            <button
              type="button"
              className="modal-x"
              aria-label="Close"
              onClick={() => setHelpOpen(false)}
            >
              ✕
            </button>
            <div className="modal-avatar">
              <img src="/plates/msg-avatar.png" alt="" />
            </div>
            <h2 id="effort-help-title">Let me think about that ☕</h2>
            <p>
              Reasoning effort is how long I let myself think before I answer. Turn it up and I'll take my time, making fewer silly mistakes and usually giving a smarter, more helpful reply. Turn it down and I'll answer quickly, which is perfect for little things like "what's 2+2?". For tricky questions, like math, code, or planning, give me a bit more effort and I'll do a much better job. The lazier the question, the lighter I can go! 💕
            </p>
            <div className="modal-actions">
              <button type="button" className="btn-gotit" onClick={() => setHelpOpen(false)}>
                Got it, grazie!
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
