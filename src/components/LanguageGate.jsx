import { useEffect, useState } from 'react'
import { useLanguage } from '../i18n/LanguageProvider'
import { LanguagePicker } from './LanguagePicker'

/**
 * Blocks first load until a language is picked. `forceOpen` reuses the same
 * dialog for the landing-page globe button, where it can also be dismissed.
 * Picking a language applies (and saves) it immediately; Continue just closes.
 */
export function LanguageGate({ forceOpen = false, onClose }) {
  const { lang, setLang, t, hasChosen, setPickerOpen } = useLanguage()
  // Latched at mount: choosing a language makes `hasChosen` true, but the
  // dialog must stay open until the user presses Continue.
  const [open, setOpen] = useState(forceOpen || !hasChosen)

  useEffect(() => {
    if (!open) return
    setPickerOpen(true)
    return () => setPickerOpen(false)
  }, [open, setPickerOpen])

  if (!open) return null

  function close() {
    // Confirm the current language even if the user keeps the default.
    setLang(lang)
    setOpen(false)
    onClose?.()
  }

  return (
    <div
      className="modal-veil"
      role="dialog"
      aria-modal="true"
      aria-labelledby="language-gate-title"
      onMouseDown={(e) => forceOpen && e.target === e.currentTarget && close()}
    >
      <div className="modal alpha-gate">
        <h2 className="alpha-gate-title" id="language-gate-title">
          {t('language.title')}
        </h2>
        <p>{t('language.subtitle')}</p>
        <LanguagePicker value={lang} onChange={setLang} />
        <button type="button" className="landing-cta alpha-gate-button" onClick={close} autoFocus>
          {t('language.continue')}
        </button>
      </div>
    </div>
  )
}
