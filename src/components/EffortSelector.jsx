import { useState } from 'react'
import { EFFORTS } from '../hooks/useChatController'
import { ChevronIcon } from './Icons'

export function EffortSelector({ effort, onChange, disabled }) {
  const [open, setOpen] = useState(false)
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
              <ChevronIcon />
            </div>
            <div className="effort-sub">Reasoning effort</div>
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
    </div>
  )
}
