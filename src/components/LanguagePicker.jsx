import { LANGUAGES, useLanguage } from '../i18n/LanguageProvider'

export function LanguagePicker({ value, onChange }) {
  const { t } = useLanguage()
  return (
    <div className="lang-picker" role="radiogroup" aria-label={t('language.label')}>
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          type="button"
          role="radio"
          aria-checked={value === l.code}
          lang={l.code}
          dir={l.dir}
          className={`lang-option${value === l.code ? ' active' : ''}`}
          onClick={() => onChange(l.code)}
        >
          {l.label}
        </button>
      ))}
    </div>
  )
}
