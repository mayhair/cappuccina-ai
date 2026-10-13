import { useState } from 'react'
import { useLanguage } from '../i18n/LanguageProvider'

const ACK_KEY = 'cappuccina-alpha-ack'
const ACK_VERSION = 'v1'

export function AlphaGate() {
  const { t, hasChosen, pickerOpen } = useLanguage()
  const [open, setOpen] = useState(
    () => localStorage.getItem(ACK_KEY) !== ACK_VERSION,
  )

  function acknowledge() {
    localStorage.setItem(ACK_KEY, ACK_VERSION)
    setOpen(false)
  }

  // The language picker comes first so this notice is read in the right language.
  if (!open || !hasChosen || pickerOpen) return null

  return (
    <div
      className="modal-veil"
      role="dialog"
      aria-modal="true"
      aria-labelledby="alpha-gate-title"
    >
      <div className="modal alpha-gate">
        <span className="alpha-gate-tag">{t('alpha.tag')}</span>
        <h2 className="alpha-gate-title" id="alpha-gate-title">
          {t('alpha.title')}
        </h2>
        <p className="alpha-gate-body">{t('alpha.body')}</p>
        <p className="alpha-gate-age">
          {t('alpha.age.before')}
          <strong>{t('alpha.age.strong')}</strong>
          {t('alpha.age.after')}
        </p>
        <button type="button" className="landing-cta alpha-gate-button" onClick={acknowledge} autoFocus>
          {t('alpha.ok')}
        </button>
      </div>
    </div>
  )
}
