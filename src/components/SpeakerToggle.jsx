import { SpeakerIcon, SpeakerOffIcon } from './Icons'

export function SpeakerToggle({ enabled, onChange }) {
  return (
    <button
      type="button"
      className={`ctrl-btn has-tip${enabled ? ' is-on' : ''}`}
      data-tip={enabled ? 'Voice replies: on' : 'Voice replies: off'}
      aria-label={enabled ? 'Turn voice replies off' : 'Turn voice replies on'}
      aria-pressed={enabled}
      onClick={() => onChange(!enabled)}
    >
      {enabled ? <SpeakerIcon /> : <SpeakerOffIcon />}
    </button>
  )
}
