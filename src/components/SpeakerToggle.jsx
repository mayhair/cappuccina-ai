import { SpeakerIcon, SpeakerOffIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'

export function SpeakerToggle({ enabled, onChange }) {
  const { t } = useLanguage()
  return (
    <button
      type="button"
      className={`ctrl-btn has-tip${enabled ? ' is-on' : ''}`}
      data-tip={enabled ? t('voice.on') : t('voice.off')}
      aria-label={enabled ? t('voice.turnOff') : t('voice.turnOn')}
      aria-pressed={enabled}
      onClick={() => onChange(!enabled)}
    >
      {enabled ? <SpeakerIcon /> : <SpeakerOffIcon />}
    </button>
  )
}
