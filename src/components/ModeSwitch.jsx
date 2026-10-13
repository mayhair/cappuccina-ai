import { useLanguage } from '../i18n/LanguageProvider'

export function ModeSwitch({ mode, onMode, disabled }) {
  const { t } = useLanguage()
  const tutor = mode === 'tutor'
  return (
    <div className="mode-switch">
      <div className="mode-seg" role="group" aria-label={t('mode.group')}>
        <button
          type="button"
          className={`seg${!tutor ? ' active' : ''}`}
          aria-pressed={!tutor}
          disabled={disabled}
          onClick={() => onMode('chat', null)}
        >
          {t('mode.chat')}
        </button>
        <button
          type="button"
          className={`seg${tutor ? ' active' : ''}`}
          aria-pressed={tutor}
          disabled={disabled}
          onClick={() => onMode('tutor', null)}
        >
          {t('mode.tutor')}
        </button>
      </div>
    </div>
  )
}
