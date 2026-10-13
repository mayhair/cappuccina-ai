import { useEffect, useState } from 'react'
import { useLanguage } from '../i18n/LanguageProvider'

const HIDE_KEY = 'cappuccina-save-webpage-notice'

export function shouldShowSaveNotice() {
  try {
    return localStorage.getItem(HIDE_KEY) !== 'hidden'
  } catch {
    return true
  }
}

// Explains what the saved zip is for the first time (and every time, until the
// user ticks "Don't show again"). Rendered inside the canvas so it stays above
// the fullscreen layer.
export function SaveWebpageNotice({ onConfirm, onCancel }) {
  const { t } = useLanguage()
  const [hideNext, setHideNext] = useState(false)

  // Escape closes the notice without downloading, same as the X and a click outside.
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onCancel])

  function confirm() {
    if (hideNext) {
      try {
        localStorage.setItem(HIDE_KEY, 'hidden')
      } catch {
        // storage blocked: the notice just shows again next time
      }
    }
    onConfirm()
  }

  return (
    <div
      className="modal-veil canvas-notice-veil"
      role="dialog"
      aria-modal="true"
      aria-labelledby="save-notice-title"
      onMouseDown={(e) => e.target === e.currentTarget && onCancel()}
    >
      <div className="modal alpha-gate">
        <button
          type="button"
          className="modal-x"
          aria-label={t('common.close')}
          onClick={onCancel}
        >
          ✕
        </button>
        <h2 className="alpha-gate-title" id="save-notice-title">
          {t('saveNotice.title')}
        </h2>
        <p className="alpha-gate-body">{t('saveNotice.body')}</p>
        <p className="alpha-gate-age">{t('saveNotice.host')}</p>
        <label className="save-notice-check">
          <input
            type="checkbox"
            checked={hideNext}
            onChange={(e) => setHideNext(e.target.checked)}
          />
          {t('saveNotice.dontShow')}
        </label>
        <button type="button" className="landing-cta alpha-gate-button" onClick={confirm} autoFocus>
          {t('alpha.ok')}
        </button>
      </div>
    </div>
  )
}
