import { useEffect, useState } from 'react'
import { CapIcon, ChatIcon, CheckIcon, ChevronIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

const MODES = [
  { value: 'chat', icon: ChatIcon },
  { value: 'tutor', icon: CapIcon },
]

// A compact chip showing the chat mode; it opens an upward picker that says
// what each mode does.
export function ModeSwitch({ mode, onMode, disabled }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)
  const current = mode === 'tutor' ? 'tutor' : 'chat'
  const CurrentIcon = MODES.find((m) => m.value === current).icon

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="mode-switch">
      <button
        type="button"
        className={`effort-pill mode-chip${current === 'tutor' ? ' is-tutor' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`${t('mode.group')}: ${t(`mode.${current}`)}`}
        disabled={disabled}
      >
        <CurrentIcon className="mode-chip-icon" aria-hidden="true" />
        <span>{t(`mode.${current}`)}</span>
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
          <div className="effort-pop mode-pop" role="dialog" aria-label={t('mode.group')}>
            {MODES.map(({ value, icon: Icon }) => (
              <button
                key={value}
                type="button"
                className={`mode-option${value === current ? ' on' : ''}`}
                aria-pressed={value === current}
                onClick={() => {
                  if (value !== current) onMode(value, null)
                  setOpen(false)
                }}
              >
                <Icon className="mode-option-icon" aria-hidden="true" />
                <span className="mode-option-text">
                  <strong>{t(`mode.${value}`)}</strong>
                  <span>{t(`mode.${value}Desc`)}</span>
                </span>
                {value === current && <CheckIcon className="mode-option-check" aria-hidden="true" />}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
