import { useEffect, useState } from 'react'
import { DotsIcon, GearIcon } from './Icons'
import { ThemeChoice, VoiceSwitch } from './AppearanceControls'
import { useLanguage } from '../i18n/LanguageProvider'

// The ⋯ menu at the end of the top bar: set-and-forget preferences live here
// so the bar itself stays one calm row.
export function TopBarMenu({ theme, onTheme, voice, onVoice, onSettings }) {
  const { t } = useLanguage()
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="top-menu">
      <button
        type="button"
        className={`ctrl-btn has-tip${open ? ' is-on' : ''}`}
        data-tip={t('app.moreOptions')}
        aria-label={t('app.moreOptions')}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
      >
        <DotsIcon />
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
          <div className="top-menu-pop" role="dialog" aria-label={t('app.moreOptions')}>
            <p className="top-menu-label">{t('theme.group')}</p>
            <ThemeChoice value={theme} onChange={onTheme} />
            {onVoice && (
              <div className="top-menu-row">
                <VoiceSwitch enabled={voice} onChange={onVoice} />
              </div>
            )}
            <div className="top-menu-sep" />
            <button
              type="button"
              className="user-menu-item"
              onClick={() => {
                setOpen(false)
                onSettings()
              }}
            >
              <GearIcon />
              {t('settings.title')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
