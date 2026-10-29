import { useEffect, useState } from 'react'
import { PencilIcon, TrashIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'
import {
  createMyCharacter,
  deleteMyCharacter,
  listMyCharacters,
  updateMyCharacter,
} from '../api/storyClient'

const NAME_MAX = 80
const TEXT_MAX = 1500

// Name, description and voice for a story character. Only the name is
// required: a fandom character can be just a name she already knows.
// Not a <form>: Enter in a one-line field saves, and Escape cancels the form
// without closing the Settings dialog around it.
export function CharacterForm({ initial, onSave, onCancel, saving }) {
  const { t } = useLanguage()
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [voice, setVoice] = useState(initial?.voice ?? '')
  const changed =
    name.trim() !== (initial?.name ?? '') ||
    description.trim() !== (initial?.description ?? '') ||
    voice.trim() !== (initial?.voice ?? '')

  const save = () => {
    if (saving || !name.trim() || !changed) return
    onSave({ name: name.trim(), description: description.trim(), voice: voice.trim() })
  }

  return (
    <div
      className="character-form"
      role="group"
      aria-label={t('story.character.name')}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          // Cancel the form, not the dialog around it.
          e.stopPropagation()
          onCancel?.()
        } else if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
          e.preventDefault()
          save()
        }
      }}
    >
      <label>
        <span>{t('story.character.name')}</span>
        <input
          className="settings-input"
          type="text"
          value={name}
          maxLength={NAME_MAX}
          autoFocus
          placeholder={t('story.character.namePlaceholder')}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <label>
        <span>{t('story.character.description')}</span>
        <textarea
          className="settings-input"
          rows={3}
          value={description}
          maxLength={TEXT_MAX}
          placeholder={t('story.character.descriptionPlaceholder')}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <label>
        <span>{t('story.character.voice')}</span>
        <input
          className="settings-input"
          type="text"
          value={voice}
          maxLength={TEXT_MAX}
          placeholder={t('story.character.voicePlaceholder')}
          onChange={(e) => setVoice(e.target.value)}
        />
      </label>
      <p className="settings-hint">{t('story.character.hint')}</p>
      <div className="memory-actions">
        <button type="button" className="settings-save" onClick={save} disabled={saving || !name.trim() || !changed}>
          {saving ? t('settings.memory.saving') : t('settings.memory.save')}
        </button>
        {onCancel && (
          <button type="button" className="memory-quiet" onClick={onCancel}>
            {t('common.cancel')}
          </button>
        )}
      </div>
    </div>
  )
}

const CHANGED_EVENT = 'story-characters-changed'

// Tells every mounted character list to reload, e.g. a story's cast picker
// after the user edited a character in Settings.
export function announceCharactersChanged() {
  window.dispatchEvent(new Event(CHANGED_EVENT))
}

// The user's saved characters, loaded on mount and again whenever another
// list announces a change. `setCharacters` updates this list in place.
export function useMyCharacters() {
  const [characters, setCharacters] = useState(null)
  const [loadFailed, setLoadFailed] = useState(false)
  useEffect(() => {
    let canceled = false
    const load = () =>
      listMyCharacters()
        .then((d) => !canceled && setCharacters(d.characters))
        .catch(() => !canceled && setLoadFailed(true))
    load()
    window.addEventListener(CHANGED_EVENT, load)
    return () => {
      canceled = true
      window.removeEventListener(CHANGED_EVENT, load)
    }
  }, [])
  return { characters, setCharacters, loadFailed }
}

// Settings > Story mode: every saved character, editable and deletable, plus
// a form to add one.
export function CharacterManager() {
  const { t } = useLanguage()
  const { characters, setCharacters, loadFailed } = useMyCharacters()
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState(null) // an id, 'new', or null
  const [busy, setBusy] = useState(false)
  const [confirmId, setConfirmId] = useState(null)

  const run = async (action) => {
    setBusy(true)
    setError('')
    try {
      await action()
      announceCharactersChanged()
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    } finally {
      setBusy(false)
    }
  }
  const sorted = (list) => [...list].sort((a, b) => a.name.localeCompare(b.name))

  return (
    <section className="settings-section">
      <h4>{t('story.character.library')}</h4>
      <div className="settings-form">
        <p className="settings-hint">{t('story.character.libraryHint')}</p>
        {error && <p className="settings-msg error" role="alert">{error}</p>}
        {loadFailed ? (
          <p className="settings-msg error" role="alert">{t('story.character.loadFailed')}</p>
        ) : characters === null ? (
          <p className="settings-hint">{t('settings.memory.loading')}</p>
        ) : characters.length === 0 ? (
          <p className="settings-hint">{t('story.character.empty')}</p>
        ) : (
          <ul className="memory-list">
            {characters.map((c) => (
              <li key={c.id} className="memory-row character-row">
                {editingId === c.id ? (
                  <CharacterForm
                    initial={c}
                    saving={busy}
                    onCancel={() => setEditingId(null)}
                    onSave={(fields) =>
                      run(async () => {
                        const row = await updateMyCharacter(c.id, fields)
                        setCharacters((list) => sorted(list.map((x) => (x.id === c.id ? row : x))))
                        setEditingId(null)
                      })
                    }
                  />
                ) : (
                  <>
                    <p className="memory-text">
                      <strong>{c.name}</strong>
                      {c.description && <span className="character-desc">{c.description}</span>}
                    </p>
                    {confirmId === c.id ? (
                      <div className="memory-actions" role="group" aria-label={t('story.character.deleteConfirm')}>
                        <span className="memory-confirm">{t('story.character.deleteConfirm')}</span>
                        <button
                          type="button"
                          className="memory-danger"
                          onClick={() =>
                            run(async () => {
                              try {
                                await deleteMyCharacter(c.id)
                              } catch (err) {
                                if (err.status !== 404) throw err
                              }
                              setCharacters((list) => list.filter((x) => x.id !== c.id))
                              setConfirmId(null)
                            })
                          }
                        >
                          {t('settings.memory.deleteYes')}
                        </button>
                        <button type="button" className="memory-quiet" onClick={() => setConfirmId(null)}>
                          {t('settings.memory.deleteNo')}
                        </button>
                      </div>
                    ) : (
                      <div className="memory-actions">
                        <button
                          type="button"
                          className="memory-icon-btn"
                          title={t('settings.memory.edit')}
                          aria-label={t('story.character.editLabel', { name: c.name })}
                          onClick={() => {
                            setEditingId(c.id)
                            setConfirmId(null)
                          }}
                        >
                          <PencilIcon />
                        </button>
                        <button
                          type="button"
                          className="memory-icon-btn danger"
                          title={t('settings.memory.delete')}
                          aria-label={t('story.character.deleteLabel', { name: c.name })}
                          onClick={() => {
                            setConfirmId(c.id)
                            setEditingId(null)
                          }}
                        >
                          <TrashIcon />
                        </button>
                      </div>
                    )}
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
        {characters !== null &&
          (editingId === 'new' ? (
            <CharacterForm
              saving={busy}
              onCancel={() => setEditingId(null)}
              onSave={(fields) =>
                run(async () => {
                  const row = await createMyCharacter(fields)
                  setCharacters((list) => sorted([...list, row]))
                  setEditingId(null)
                })
              }
            />
          ) : (
            <div className="settings-actions">
              <button type="button" className="settings-save" onClick={() => setEditingId('new')}>
                {t('story.character.new')}
              </button>
            </div>
          ))}
      </div>
    </section>
  )
}
