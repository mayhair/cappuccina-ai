import { useState } from 'react'
import { EFFORTS } from '../hooks/useChatController'
import { ChevronIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

export function EffortSelector({ effort, onChange, disabled }) {
  const { t, tr } = useLanguage()
  const [open, setOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const index = Math.max(0, EFFORTS.findIndex((e) => e.label === effort))
  const label = (e) => t(`effort.${e.value}`)
  const current = label(EFFORTS[index])
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
        aria-label={t('effort.pillLabel', { effort: current })}
        disabled={disabled}
      >
        <span>{current}</span>
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
          <div className="effort-pop" role="dialog" aria-label={t('effort.select')}>
            <div className="effort-name">
              <span>{current}</span>
            </div>
            <div className="effort-sub">
              <span>{t('effort.title')}</span>
              <button
                type="button"
                className="effort-help"
                onClick={() => setHelpOpen(true)}
              >
                {t('effort.what')}
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
              aria-label={t('effort.title')}
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
                  {label(e)}
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
              aria-label={t('common.close')}
              onClick={() => setHelpOpen(false)}
            >
              ✕
            </button>
            <div className="modal-avatar">
              <img src="/plates/msg-avatar.webp" alt="" />
            </div>
            <h2 id="effort-help-title">{t('effort.help.title')}</h2>
            <p>{t('effort.help.body')}</p>
            <div className="modal-actions">
              <button type="button" className="btn-gotit" onClick={() => setHelpOpen(false)}>
                {t('effort.help.ok')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
