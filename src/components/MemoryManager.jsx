import { useEffect, useState } from 'react'
import { PencilIcon, TrashIcon } from './Icons'
import { useLanguage } from '../i18n/LanguageProvider'
import { setMemoryEnabled } from '../api/authClient'
import {
  createMemory,
  deleteAllMemories,
  deleteMemory,
  listMemories,
  updateMemory,
} from '../api/memoryClient'

// The Memory section of Settings: the on/off switch plus every memory the AI
// (or the user) has saved, each one editable and deletable by hand. Deleting
// asks inline ("Delete this memory? Yes / Keep it") instead of opening a modal,
// because the app's confirm modal renders underneath the settings dialog.
export function MemoryManager({ user, onUserUpdated }) {
  const { t } = useLanguage()
  // null while loading; otherwise { memories, limit, maxChars }
  const [data, setData] = useState(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [error, setError] = useState('')
  const [toggleBusy, setToggleBusy] = useState(false)
  const [newText, setNewText] = useState('')
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [editText, setEditText] = useState('')
  const [savingId, setSavingId] = useState(null)
  const [confirmId, setConfirmId] = useState(null)
  const [confirmAll, setConfirmAll] = useState(false)
  const enabled = user.memoryEnabled !== false

  useEffect(() => {
    let canceled = false
    listMemories()
      .then((d) => {
        if (!canceled) setData(d)
      })
      .catch(() => {
        if (!canceled) setLoadFailed(true)
      })
    return () => {
      canceled = true
    }
  }, [])

  const memories = data?.memories ?? []
  const maxChars = data?.maxChars ?? 300
  const full = data ? memories.length >= data.limit : false

  // Saved or added memories become the newest, so they move to the top.
  const putFirst = (memory) =>
    setData((d) => ({ ...d, memories: [memory, ...d.memories.filter((m) => m.id !== memory.id)] }))
  const remove = (id) =>
    setData((d) => ({ ...d, memories: d.memories.filter((m) => m.id !== id) }))

  const toggle = async () => {
    if (toggleBusy) return
    setToggleBusy(true)
    setError('')
    try {
      onUserUpdated(await setMemoryEnabled(!enabled))
    } catch (err) {
      setError(err.message || t('settings.memory.togglingFailed'))
    } finally {
      setToggleBusy(false)
    }
  }

  const add = async (e) => {
    e.preventDefault()
    const text = newText.trim()
    if (!text || adding) return
    setAdding(true)
    setError('')
    try {
      putFirst(await createMemory(text))
      setNewText('')
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    } finally {
      setAdding(false)
    }
  }

  const startEdit = (memory) => {
    setEditingId(memory.id)
    setEditText(memory.content)
    setConfirmId(null)
    setError('')
  }

  const saveEdit = async (e) => {
    e.preventDefault()
    const text = editText.trim()
    if (!text || savingId) return
    setSavingId(editingId)
    setError('')
    try {
      putFirst(await updateMemory(editingId, text))
      setEditingId(null)
    } catch (err) {
      if (err.status === 404) {
        remove(editingId)
        setEditingId(null)
      }
      setError(err.message || t('settings.error.generic'))
    } finally {
      setSavingId(null)
    }
  }

  const confirmDelete = async (id) => {
    setError('')
    try {
      await deleteMemory(id)
      remove(id)
      setConfirmId(null)
    } catch (err) {
      if (err.status === 404) {
        // Already gone (deleted from the chat card or another tab): same result.
        remove(id)
        setConfirmId(null)
      } else {
        setError(err.message || t('settings.error.generic'))
      }
    }
  }

  const confirmDeleteAll = async () => {
    setError('')
    try {
      await deleteAllMemories()
      setData((d) => ({ ...d, memories: [] }))
      setConfirmAll(false)
    } catch (err) {
      setError(err.message || t('settings.error.generic'))
    }
  }

  return (
    <>
      <section className="settings-section">
        <h4>{t('settings.memory.useMemory')}</h4>
        <div className="settings-form">
          <label className="memory-switch">
            <input
              type="checkbox"
              role="switch"
              checked={enabled}
              onChange={toggle}
              disabled={toggleBusy}
            />
            <span className="memory-switch-track" aria-hidden="true" />
            <span>{t('settings.memory.useMemory')}</span>
          </label>
          <p className="settings-hint">{t('settings.memory.toggleHint')}</p>
        </div>
      </section>

      <section className="settings-section">
        <h4>{t('settings.memory.saved')}</h4>
        <div className="settings-form">
          {!enabled && <p className="settings-hint">{t('settings.memory.paused')}</p>}
          {error && (
            <p className="settings-msg error" role="alert">
              {error}
            </p>
          )}

          {loadFailed ? (
            <p className="settings-msg error" role="alert">
              {t('settings.memory.loadFailed')}
            </p>
          ) : data === null ? (
            <p className="settings-hint">{t('settings.memory.loading')}</p>
          ) : memories.length === 0 ? (
            <p className="settings-hint">{t('settings.memory.empty')}</p>
          ) : (
            <>
              <p className="settings-hint memory-count">
                {t('settings.memory.count', { count: memories.length, limit: data.limit })}
              </p>
              <ul className={`memory-list${enabled ? '' : ' paused'}`}>
                {memories.map((memory) => (
                  <li key={memory.id} className="memory-row">
                    {editingId === memory.id ? (
                      <form className="memory-edit" onSubmit={saveEdit}>
                        <input
                          className="settings-input memory-input"
                          type="text"
                          value={editText}
                          maxLength={maxChars}
                          autoFocus
                          aria-label={t('settings.memory.editField')}
                          onChange={(e) => setEditText(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Escape') {
                              // Cancel the edit, not the whole settings dialog.
                              e.stopPropagation()
                              setEditingId(null)
                            }
                          }}
                        />
                        <div className="memory-actions">
                          <button
                            type="submit"
                            className="settings-save"
                            disabled={
                              savingId !== null ||
                              !editText.trim() ||
                              editText.trim() === memory.content
                            }
                          >
                            {savingId === memory.id
                              ? t('settings.memory.saving')
                              : t('settings.memory.save')}
                          </button>
                          <button
                            type="button"
                            className="memory-quiet"
                            onClick={() => setEditingId(null)}
                          >
                            {t('common.cancel')}
                          </button>
                        </div>
                      </form>
                    ) : (
                      <>
                        <p className="memory-text">{memory.content}</p>
                        {confirmId === memory.id ? (
                          <div className="memory-actions" role="group" aria-label={t('settings.memory.deleteConfirm')}>
                            <span className="memory-confirm">{t('settings.memory.deleteConfirm')}</span>
                            <button
                              type="button"
                              className="memory-danger"
                              onClick={() => confirmDelete(memory.id)}
                            >
                              {t('settings.memory.deleteYes')}
                            </button>
                            <button
                              type="button"
                              className="memory-quiet"
                              onClick={() => setConfirmId(null)}
                            >
                              {t('settings.memory.deleteNo')}
                            </button>
                          </div>
                        ) : (
                          <div className="memory-actions">
                            <button
                              type="button"
                              className="memory-icon-btn"
                              aria-label={t('settings.memory.editLabel', { text: memory.content })}
                              title={t('settings.memory.edit')}
                              onClick={() => startEdit(memory)}
                            >
                              <PencilIcon />
                            </button>
                            <button
                              type="button"
                              className="memory-icon-btn danger"
                              aria-label={t('settings.memory.deleteLabel', { text: memory.content })}
                              title={t('settings.memory.delete')}
                              onClick={() => {
                                setConfirmId(memory.id)
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

              {confirmAll ? (
                <div className="memory-actions memory-all" role="group">
                  <span className="memory-confirm">
                    {t('settings.memory.deleteAllConfirm', { count: memories.length })}
                  </span>
                  <button type="button" className="memory-danger" onClick={confirmDeleteAll}>
                    {t('settings.memory.deleteAllYes')}
                  </button>
                  <button type="button" className="memory-quiet" onClick={() => setConfirmAll(false)}>
                    {t('settings.memory.deleteNo')}
                  </button>
                </div>
              ) : (
                <div className="settings-actions">
                  <button
                    type="button"
                    className="memory-quiet danger"
                    onClick={() => setConfirmAll(true)}
                  >
                    {t('settings.memory.deleteAll')}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </section>

      <section className="settings-section">
        <h4>{t('settings.memory.add')}</h4>
        <form className="settings-form" onSubmit={add}>
          <div className="memory-add">
            <input
              className="settings-input memory-input"
              type="text"
              value={newText}
              maxLength={maxChars}
              placeholder={t('settings.memory.addPlaceholder')}
              aria-label={t('settings.memory.add')}
              onChange={(e) => setNewText(e.target.value)}
              disabled={data === null || full}
            />
            <button
              type="submit"
              className="settings-save"
              disabled={adding || !newText.trim() || data === null || full}
            >
              {adding ? t('settings.memory.adding') : t('settings.memory.addButton')}
            </button>
          </div>
        </form>
      </section>
    </>
  )
}
