export function ModeSwitch({ mode, onMode, disabled }) {
  const tutor = mode === 'tutor'
  return (
    <div className="mode-switch">
      <div className="mode-seg" role="group" aria-label="Chat mode">
        <button
          type="button"
          className={`seg${!tutor ? ' active' : ''}`}
          aria-pressed={!tutor}
          disabled={disabled}
          onClick={() => onMode('chat', null)}
        >
          Chat
        </button>
        <button
          type="button"
          className={`seg${tutor ? ' active' : ''}`}
          aria-pressed={tutor}
          disabled={disabled}
          onClick={() => onMode('tutor', null)}
        >
          Tutor
        </button>
      </div>
    </div>
  )
}
