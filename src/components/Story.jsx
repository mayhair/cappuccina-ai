import { useEffect, useState } from 'react'
import { GearIcon, PencilIcon, RetryIcon, TrashIcon } from './Icons'
import { useMyCharacters } from './CharacterManager'
import { useLanguage } from '../i18n/LanguageProvider'
import { STORY_WORLDS, storyWorld } from '../lib/storyWorlds'
import {
  createChapter,
  deleteChapter,
  listChapters,
  listCharacters,
  resummarizeChapter,
  updateChapter,
} from '../api/storyClient'

// The lore book's cast, fetched once per page load and shared by every picker.
let charactersPromise = null
function useLoreCharacters() {
  const [characters, setCharacters] = useState([])
  useEffect(() => {
    let canceled = false
    charactersPromise ??= listCharacters().catch((err) => {
      charactersPromise = null
      throw err
    })
    charactersPromise.then((list) => !canceled && setCharacters(list)).catch(() => {})
    return () => {
      canceled = true
    }
  }, [])
  return characters
}

function Chip({ on, onClick, title, disabled, children }) {
  return (
    <button
      type="button"
      className={`story-chip${on ? ' on' : ''}`}
      aria-pressed={on}
      title={title}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

const toggled = (list, id) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

// Who is in the scene: Cappuccina herself (a switch), the user's saved
// characters (made and edited in Settings), and the lore book's cast.
// Outside her world the lore cast is tucked behind a disclosure.
function CastEditor({ story, onChange, onManageCharacters, disabled }) {
  const { t } = useLanguage()
  const lore = useLoreCharacters()
  const { characters: mine } = useMyCharacters()
  const herWorld = (story.world ?? 'cappuccina') === 'cappuccina'
  const cast = story.cast ?? []
  const picked = story.characters ?? []
  // The character the user plays is theirs to voice, not part of the cast.
  const others = (mine ?? []).filter((c) => c.id !== story.playerCharacter)
  const [loreOpen, setLoreOpen] = useState(herWorld || cast.length > 0)

  return (
    <div className="story-cast-editor">
      <label className="memory-switch story-switch">
        <input
          type="checkbox"
          role="switch"
          checked={story.withCappuccina !== false}
          disabled={disabled}
          onChange={(e) => onChange({ ...story, withCappuccina: e.target.checked })}
        />
        <span className="memory-switch-track" aria-hidden="true" />
        <span>{t('story.withCappuccina')}</span>
      </label>
      <p className="settings-hint story-switch-hint">
        {story.withCappuccina !== false ? t('story.withCappuccinaOn') : t('story.withCappuccinaOff')}
      </p>

      <h3 className="story-label">{t('story.yourCharacters')}</h3>
      {onManageCharacters && (
        <button type="button" className="story-manage" onClick={onManageCharacters}>
          <GearIcon aria-hidden="true" />
          {t('story.character.manage')}
        </button>
      )}
      {others.length > 0 && (
        <div className="story-cast" role="group" aria-label={t('story.yourCharacters')}>
          {others.map((c) => (
            <Chip
              key={c.id}
              on={picked.includes(c.id)}
              title={c.description}
              disabled={disabled}
              onClick={() => onChange({ ...story, characters: toggled(picked, c.id) })}
            >
              {c.name}
            </Chip>
          ))}
        </div>
      )}

      {lore.length > 0 && (
        <>
          <button
            type="button"
            className="story-disclosure"
            aria-expanded={loreOpen}
            onClick={() => setLoreOpen((o) => !o)}
          >
            {t('story.fromHerWorld')} {loreOpen ? '▾' : '▸'}
          </button>
          {loreOpen && (
            <div className="story-cast" role="group" aria-label={t('story.fromHerWorld')}>
              {lore.map((c) => (
                <Chip
                  key={c.id}
                  on={cast.includes(c.id)}
                  title={c.blurb}
                  disabled={disabled}
                  onClick={() => onChange({ ...story, cast: toggled(cast, c.id) })}
                >
                  {c.name}
                  {c.hasLook && <span className="story-chip-look" aria-label={t('story.officialLook')}>✦</span>}
                </Chip>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// The setup a world starts with: its first scene, that scene's lore cast, and
// whether Cappuccina is in it by default.
function worldDefaults(world, previous) {
  const scene = world.scenes[0]
  return {
    world: world.id,
    worldText: '',
    scenario: '',
    withCappuccina: world.withCappuccina === true,
    cast: scene?.cast ?? [],
    characters: previous?.characters ?? [],
    player: previous?.player ?? '',
    playerCharacter: previous?.playerCharacter ?? null,
  }
}

// Who the user plays: one of their saved characters (picked like a radio,
// click again to unpick), plus free-form notes. A button to Settings always
// leads, as it does under "Your characters".
function PlayerPicker({ story, onChange, onManageCharacters, disabled }) {
  const { t } = useLanguage()
  const { characters: mine } = useMyCharacters()
  const playing = mine?.find((c) => c.id === story.playerCharacter)

  const pick = (id) =>
    onChange((s) =>
      s.playerCharacter === id
        ? { ...s, playerCharacter: null }
        : // Playing a character takes them out of the cast she voices.
          { ...s, playerCharacter: id, characters: (s.characters ?? []).filter((x) => x !== id) },
    )

  return (
    <div className="story-player">
      {onManageCharacters && (
        <button type="button" className="story-manage" onClick={onManageCharacters}>
          <GearIcon aria-hidden="true" />
          {t('story.playerCreate')}
        </button>
      )}
      {mine?.length > 0 && (
        <>
          <p className="settings-hint">{t('story.playerHint')}</p>
          <div className="story-cast" role="radiogroup" aria-label={t('story.player')}>
            {mine.map((c) => (
              <button
                key={c.id}
                type="button"
                role="radio"
                aria-checked={story.playerCharacter === c.id}
                className={`story-chip${story.playerCharacter === c.id ? ' on' : ''}`}
                title={c.description}
                disabled={disabled}
                onClick={() => pick(c.id)}
              >
                {c.name}
              </button>
            ))}
          </div>
        </>
      )}
      <textarea
        className="settings-input story-own"
        rows={2}
        maxLength={1000}
        value={story.player ?? ''}
        disabled={disabled}
        placeholder={
          playing ? t('story.playerNotesPlaceholder', { name: playing.name }) : t('story.playerPlaceholder')
        }
        aria-label={playing ? t('story.playerNotes') : t('story.player')}
        onChange={(e) => onChange((s) => ({ ...s, player: e.target.value }))}
      />
    </div>
  )
}

// Shown in an empty story chat: pick a world, an opening and a cast; then the
// opening line goes out as the first message.
export function StoryStart({ onStart, onManageCharacters, disabled }) {
  const { t } = useLanguage()
  const [story, setStory] = useState(() => worldDefaults(STORY_WORLDS[0]))
  const [sceneId, setSceneId] = useState(STORY_WORLDS[0].scenes[0].id)
  const [custom, setCustom] = useState('')
  const world = storyWorld(story.world)
  const own = sceneId === 'own'

  const pickWorld = (w) => {
    setStory((s) => worldDefaults(w, s))
    setSceneId(w.scenes[0]?.id ?? 'own')
    setCustom('')
  }
  const pickScene = (scene) => {
    setSceneId(scene.id)
    setStory((s) => ({ ...s, cast: scene.cast ?? s.cast }))
  }

  const scenario = own ? custom.trim() : t(`story.scene.${sceneId}.setup`)
  const needsWorldText = world.needsText && !story.worldText.trim()
  // Fandom and custom worlds can start from the world alone.
  const ready = !needsWorldText && (scenario || world.needsText)

  const start = (e) => {
    e.preventDefault()
    if (!ready) return
    const title = own
      ? scenario || story.worldText.trim()
      : t(`story.scene.${sceneId}.title`)
    onStart(t('story.opening', { scene: title }), { ...story, scenario })
  }

  return (
    <form className="story-start" onSubmit={start}>
      <h1 className="welcome-title">{t('story.startTitle')}</h1>
      <p className="welcome-sub">{t('story.startSub')}</p>

      <h2 className="story-label">{t('story.step.world')}</h2>
      <div className="story-worlds" role="radiogroup" aria-label={t('story.step.world')}>
        {STORY_WORLDS.map((w) => (
          <button
            key={w.id}
            type="button"
            role="radio"
            aria-checked={story.world === w.id}
            className={`story-world${story.world === w.id ? ' on' : ''}`}
            onClick={() => pickWorld(w)}
          >
            <span className="story-world-emoji" aria-hidden="true">{w.emoji}</span>
            <strong>{t(`story.world.${w.id}.title`)}</strong>
            <span>{t(`story.world.${w.id}.blurb`)}</span>
          </button>
        ))}
      </div>
      {world.needsText && (
        <textarea
          className="settings-input story-own"
          rows={3}
          maxLength={2000}
          value={story.worldText}
          placeholder={t(`story.world.${world.id}.placeholder`)}
          aria-label={t(`story.world.${world.id}.title`)}
          onChange={(e) => setStory((s) => ({ ...s, worldText: e.target.value }))}
        />
      )}

      <h2 className="story-label">{t('story.step.opening')}</h2>
      <div className="story-scenes" role="radiogroup" aria-label={t('story.step.opening')}>
        {world.scenes.map((scene) => (
          <button
            key={scene.id}
            type="button"
            role="radio"
            aria-checked={sceneId === scene.id}
            className={`story-scene${sceneId === scene.id ? ' on' : ''}`}
            onClick={() => pickScene(scene)}
          >
            <strong>{t(`story.scene.${scene.id}.title`)}</strong>
            <span>{t(`story.scene.${scene.id}.setup`)}</span>
          </button>
        ))}
        <button
          type="button"
          role="radio"
          aria-checked={own}
          className={`story-scene${own ? ' on' : ''}`}
          onClick={() => setSceneId('own')}
        >
          <strong>{t('story.ownTitle')}</strong>
          <span>{world.needsText ? t('story.ownSubOptional') : t('story.ownSub')}</span>
        </button>
      </div>
      {own && (
        <textarea
          className="settings-input story-own"
          rows={3}
          maxLength={2000}
          value={custom}
          placeholder={t('story.ownPlaceholder')}
          aria-label={t('story.ownTitle')}
          onChange={(e) => setCustom(e.target.value)}
        />
      )}
      <h2 className="story-label">{t('story.player')}</h2>
      <PlayerPicker story={story} onChange={setStory} onManageCharacters={onManageCharacters} />

      <h2 className="story-label">{t('story.step.cast')}</h2>
      <CastEditor key={story.world} story={story} onChange={setStory} onManageCharacters={onManageCharacters} />

      <button type="submit" className="settings-save story-go" disabled={disabled || !ready}>
        {t('story.begin')}
      </button>
    </form>
  )
}

// Above a running story: its world, who is in the scene, and the "Story so
// far" notes.
export function StoryBar({ chat, onStory, onManageCharacters, disabled }) {
  const { t } = useLanguage()
  const [castOpen, setCastOpen] = useState(false)
  const [notesOpen, setNotesOpen] = useState(false)
  const story = { world: 'cappuccina', withCappuccina: true, cast: [], characters: [], ...(chat.story ?? {}) }
  const world = storyWorld(story.world)
  const count = story.cast.length + story.characters.length + (story.withCappuccina !== false ? 1 : 0)
  return (
    <div className="story-bar">
      <div className="story-bar-row">
        <span className="story-bar-world">
          {world.emoji} {t(`story.world.${world.id}.title`)}
        </span>
        <button
          type="button"
          className="story-bar-btn"
          aria-expanded={castOpen}
          onClick={() => setCastOpen((o) => !o)}
        >
          {t('story.castCount', { count })}
        </button>
        <button
          type="button"
          className="story-bar-btn"
          onClick={() => setNotesOpen(true)}
          disabled={chat.pending}
        >
          {t('story.soFar')}
        </button>
      </div>
      {castOpen && (
        <div className="story-bar-cast">
          <p className="settings-hint">{t('story.castHint')}</p>
          <h3 className="story-label">{t('story.player')}</h3>
          {/* PlayerPicker hands over updater functions; the bar saves plain setups. */}
          <PlayerPicker
            story={story}
            disabled={disabled}
            onChange={(update) => onStory(typeof update === 'function' ? update(story) : update)}
            onManageCharacters={onManageCharacters}
          />
          <h3 className="story-label">{t('story.step.cast')}</h3>
          <CastEditor story={story} disabled={disabled} onChange={onStory} onManageCharacters={onManageCharacters} />
        </div>
      )}
      {notesOpen && (
        <StoryChapters chat={chat} onClose={() => setNotesOpen(false)} />
      )}
    </div>
  )
}

// Suggested next moves under the newest story reply. A click sends one as the
// user's message; regenerate or edit still make sibling branches as usual.
export function StoryChoices({ options, onPick, disabled }) {
  const { t } = useLanguage()
  return (
    <div className="story-choices" role="group" aria-label={t('story.choices')}>
      {options.map((option) => (
        <button
          key={option}
          type="button"
          className="story-choice"
          disabled={disabled}
          onClick={() => onPick(option)}
        >
          {option}
        </button>
      ))}
    </div>
  )
}

// A suggest_actions tool part -> its options, or null.
export function choicesFromPart(part) {
  if (part?.name !== 'suggest_actions' || typeof part.result !== 'string') return null
  try {
    const data = JSON.parse(part.result)
    return data?.status === 'story_choices' && Array.isArray(data.options) && data.options.length
      ? data.options
      : null
  } catch {
    return null
  }
}

// Chapters that apply to the branch being viewed: summaries whose last
// message is on it, plus the user's own notes.
function onBranch(chapters, chat) {
  const path = new Set((chat.messages ?? []).map((m) => m.id))
  return chapters.filter((c) => !c.endUid || path.has(c.endUid))
}

// The "Story so far" panel: every chapter summary for this branch, editable
// and deletable like memories, plus notes the user adds by hand.
function StoryChapters({ chat, onClose }) {
  const { t } = useLanguage()
  const [data, setData] = useState(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const [busyId, setBusyId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)
  const [newText, setNewText] = useState('')

  useEffect(() => {
    let canceled = false
    listChapters(chat.id)
      .then((d) => !canceled && setData(d))
      .catch(() => !canceled && setLoadFailed(true))
    return () => {
      canceled = true
    }
  }, [chat.id])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape' && editingId === null) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editingId, onClose])

  const chapters = data ? onBranch(data.chapters, chat) : []
  const maxChars = data?.maxChars ?? 4000
  const replace = (row) =>
    setData((d) => ({ ...d, chapters: d.chapters.map((c) => (c.id === row.id ? row : c)) }))
  const remove = (id) =>
    setData((d) => ({ ...d, chapters: d.chapters.filter((c) => c.id !== id) }))

  const run = async (id, action) => {
    setBusyId(id)
    setError('')
    try {
      await action()
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    } finally {
      setBusyId(null)
    }
  }

  const saveEdit = (e) => {
    e.preventDefault()
    const text = editText.trim()
    if (!text) return
    run(editingId, async () => {
      replace(await updateChapter(chat.id, editingId, text))
      setEditingId(null)
    })
  }
  const add = (e) => {
    e.preventDefault()
    const text = newText.trim()
    if (!text) return
    run('new', async () => {
      const row = await createChapter(chat.id, text)
      setData((d) => ({ ...d, chapters: [...d.chapters, row] }))
      setNewText('')
    })
  }

  return (
    <div
      className="modal-veil"
      role="dialog"
      aria-modal="true"
      aria-labelledby="story-chapters-title"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="modal story-chapters">
        <button type="button" className="modal-x" aria-label={t('common.close')} onClick={onClose}>
          ✕
        </button>
        <h2 id="story-chapters-title">{t('story.soFar')}</h2>
        <p>{t('story.soFarHint', { size: data?.chapterSize ?? 30 })}</p>
        {error && <p className="settings-msg error" role="alert">{error}</p>}
        {loadFailed ? (
          <p className="settings-msg error" role="alert">{t('story.loadFailed')}</p>
        ) : data === null ? (
          <p className="settings-hint">{t('settings.memory.loading')}</p>
        ) : chapters.length === 0 ? (
          <p className="settings-hint">{t('story.noChapters')}</p>
        ) : (
          <ol className="memory-list story-chapter-list">
            {chapters.map((c, i) => (
              <li key={c.id} className="memory-row story-chapter">
                {editingId === c.id ? (
                  <form className="memory-edit" onSubmit={saveEdit}>
                    <textarea
                      className="settings-input memory-input"
                      rows={5}
                      value={editText}
                      maxLength={maxChars}
                      autoFocus
                      aria-label={t('story.editField')}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Escape') {
                          e.stopPropagation()
                          setEditingId(null)
                        }
                      }}
                    />
                    <div className="memory-actions">
                      <button
                        type="submit"
                        className="settings-save"
                        disabled={busyId !== null || !editText.trim() || editText.trim() === c.text}
                      >
                        {busyId === c.id ? t('settings.memory.saving') : t('settings.memory.save')}
                      </button>
                      <button type="button" className="memory-quiet" onClick={() => setEditingId(null)}>
                        {t('common.cancel')}
                      </button>
                    </div>
                  </form>
                ) : (
                  <>
                    <p className="memory-text">
                      <strong className="story-chapter-label">
                        {c.endUid ? t('story.chapter', { n: i + 1 }) : t('story.note')}
                        {c.edited && c.endUid ? ` · ${t('story.edited')}` : ''}
                      </strong>
                      {c.text}
                    </p>
                    {confirmId === c.id ? (
                      <div className="memory-actions" role="group" aria-label={t('story.deleteConfirm')}>
                        <span className="memory-confirm">{t('story.deleteConfirm')}</span>
                        <button
                          type="button"
                          className="memory-danger"
                          onClick={() =>
                            run(c.id, async () => {
                              try {
                                await deleteChapter(chat.id, c.id)
                              } catch (err) {
                                if (err.status !== 404) throw err
                              }
                              remove(c.id)
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
                          aria-label={t('story.editLabel', { n: i + 1 })}
                          onClick={() => {
                            setEditingId(c.id)
                            setEditText(c.text)
                            setConfirmId(null)
                          }}
                        >
                          <PencilIcon />
                        </button>
                        {c.endUid && (
                          <button
                            type="button"
                            className="memory-icon-btn"
                            title={t('story.resummarize')}
                            aria-label={t('story.resummarize')}
                            disabled={busyId !== null}
                            onClick={() => run(c.id, async () => replace(await resummarizeChapter(chat.id, c.id)))}
                          >
                            <RetryIcon />
                          </button>
                        )}
                        <button
                          type="button"
                          className="memory-icon-btn danger"
                          title={t('settings.memory.delete')}
                          aria-label={t('story.deleteLabel', { n: i + 1 })}
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
          </ol>
        )}
        {data !== null && (
          <form className="memory-add" onSubmit={add}>
            <input
              className="settings-input memory-input"
              type="text"
              value={newText}
              maxLength={maxChars}
              placeholder={t('story.addPlaceholder')}
              aria-label={t('story.add')}
              onChange={(e) => setNewText(e.target.value)}
            />
            <button type="submit" className="settings-save" disabled={!newText.trim() || busyId !== null}>
              {busyId === 'new' ? t('settings.memory.adding') : t('settings.memory.addButton')}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
