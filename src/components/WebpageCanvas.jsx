import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowLeftIcon,
  ChevronIcon,
  CloseIcon,
  CollapseIcon,
  DotsIcon,
  DownloadIcon,
  ExpandIcon,
  FileIcon,
  PencilIcon,
  RefreshIcon,
  WindowIcon,
} from './Icons'
import { highlightCode } from '../lib/highlightCode'
import {
  downloadWebpageZip,
  getWebpageFile,
  getWebpageVersionDiff,
  previewFileUrl,
} from '../api/webpageClient'
import { SaveWebpageNotice, shouldShowSaveNotice } from './SaveWebpageNotice'
import { useLanguage } from '../i18n/LanguageProvider'

const ENTRY_FILE = 'index.html'
const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg']

// The files the Code tab lists: the entry on top (it is what the canvas shows),
// then the rest alphabetically.
function fileList(names, entry) {
  const list = [...(names ?? [])]
  const first = list.includes(entry) ? entry : null
  return [...(first ? [first] : []), ...list.filter((name) => name !== first).sort()]
}

// A picture is shown as a picture, not as text — by its name's extension, since
// an svg is text on the wire but still an image in the pane.
function isImage(name) {
  const ext = String(name ?? '').split('.').pop().toLowerCase()
  return IMAGE_EXTENSIONS.includes(ext)
}

// How a file's size reads: "812 KB".
function formatBytes(bytes) {
  const size = Number(bytes) || 0
  if (size <= 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

// "AI run · 2 files · +8 −1" (or the edit's own label): what one iteration did.
function historyLabel(version, t) {
  if (!version) return ''
  const name = version.origin === 'manual' ? version.label || t('canvas.manualEdit') : t('canvas.aiRun')
  const summary = version.summary ?? {}
  return t('canvas.versionSummary', {
    name,
    files: summary.files ?? 0,
    added: summary.added ?? 0,
    removed: summary.removed ?? 0,
  })
}

// Timestamps read better as a clock time for today and a date for older runs.
function iterationWhen(iso) {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ''
  const today = new Date()
  const sameDay =
    when.getFullYear() === today.getFullYear() &&
    when.getMonth() === today.getMonth() &&
    when.getDate() === today.getDate()
  return new Intl.DateTimeFormat(
    undefined,
    sameDay ? { hour: '2-digit', minute: '2-digit' } : { month: 'short', day: 'numeric' },
  ).format(when)
}

function historyMeta(version, t) {
  const when = iterationWhen(version?.createdAt)
  // Isolate the time so numerals and separators keep their order in RTL.
  return when ? `${historyLabel(version, t)} · \u2068${when}\u2069` : historyLabel(version, t)
}

function CanvasTitle({ streaming }) {
  const { t } = useLanguage()
  return (
    <span className="canvas-title">
      <WindowIcon /> {t('canvas.title')}
      {streaming && <span className="canvas-heading-dots" aria-hidden="true"><i /><i /><i /></span>}
    </span>
  )
}

// The iteration stepper under the canvas head: steps through the page's runs,
// plus the full list on demand.
function IterationsBar({
  versions,
  index,
  current,
  activeVersionId,
  canSwitch,
  historyOpen,
  onToggleHistory,
  onStep,
  onSelect,
}) {
  const { t } = useLanguage()
  return (
    <div className="canvas-history" role="group" aria-label={t('canvas.iterations')}>
      <button
        type="button"
        className="canvas-history-step"
        aria-label={t('canvas.prevIteration')}
        disabled={!canSwitch || index <= 0}
        onClick={() => onStep(versions[index - 1].id)}
      >
        <ChevronIcon />
      </button>
      <div className="canvas-history-label">
        <span className="canvas-history-count">
          {t('canvas.iterationOf', { n: index + 1, total: versions.length })}
        </span>
        <span className="canvas-history-meta">{historyMeta(current, t)}</span>
      </div>
      <button
        type="button"
        className="canvas-history-step"
        aria-label={t('canvas.nextIteration')}
        disabled={!canSwitch || index < 0 || index >= versions.length - 1}
        onClick={() => onStep(versions[index + 1].id)}
      >
        <ChevronIcon />
      </button>
      <button
        type="button"
        className="canvas-history-btn"
        aria-expanded={historyOpen}
        aria-label={t('canvas.allIterations')}
        disabled={!canSwitch}
        onClick={onToggleHistory}
      >
        <DotsIcon />
      </button>
      {historyOpen && (
        <div className="canvas-history-panel" role="listbox" aria-label={t('canvas.iterations')}>
          {[...versions].reverse().map((v) => (
            <button
              key={v.id}
              type="button"
              role="option"
              aria-selected={v.id === activeVersionId}
              className={`canvas-file-row${v.id === activeVersionId ? ' active' : ''}`}
              disabled={!canSwitch}
              onClick={() => onSelect(v.id)}
            >
              <span className="canvas-history-seq">#{v.seq}</span>
              <span className="canvas-file-name">{historyLabel(v, t)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// What the preview runtime (backend app/webpage_preview.py) posts when the page
// throws or a file fails to load.
const PREVIEW_ERROR_TYPE = 'cappuccina-preview-error'
const MAX_PAGE_ERRORS = 5

// "TypeError: x is null (script.js:12)": one page error as the badge and the
// fix request show it.
function describePageError(error) {
  const where = error.source ? ` (${error.source}${error.line ? `:${error.line}` : ''})` : ''
  return `${error.message}${where}`
}

// The live page: the site as the server serves it, in a sandboxed frame. The
// frame reloads on the controller's revision (a run's writes, an activation, a
// save) and on the Refresh button's own counter. Errors the page reports while
// someone uses it show as a badge that can hand them to Cappuccina.
function PreviewTab({ previewUrl, revision, localRevision, streaming, onAskFix }) {
  const { t } = useLanguage()
  const frameRef = useRef(null)
  const [errors, setErrors] = useState([])
  const frameKey = `${revision}-${localRevision}`

  // A reload is a fresh page: its old errors no longer apply.
  useEffect(() => {
    setErrors([])
  }, [frameKey])

  useEffect(() => {
    const onMessage = (event) => {
      if (event.source !== frameRef.current?.contentWindow) return
      const data = event.data
      if (data?.type !== PREVIEW_ERROR_TYPE || typeof data.message !== 'string') return
      const error = {
        message: data.message.slice(0, 300),
        source: typeof data.source === 'string' ? data.source.slice(0, 120) : '',
        line: Number(data.line) || 0,
      }
      setErrors((all) => {
        const text = describePageError(error)
        if (all.length >= MAX_PAGE_ERRORS || all.some((e) => describePageError(e) === text)) return all
        return [...all, error]
      })
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  const askFix = () => {
    const list = errors.map((e) => `- ${describePageError(e)}`).join('\n')
    const which = errors.length === 1 ? 'this error' : 'these errors'
    onAskFix?.(`The webpage shows ${which} when I use it:\n${list}\nCan you fix it?`)
    setErrors([])
  }

  return (
    <div className="canvas-stage">
      <iframe
        ref={frameRef}
        key={frameKey}
        className="canvas-frame"
        title={t('canvas.previewTitle')}
        sandbox="allow-scripts allow-forms allow-modals"
        src={previewUrl}
      />
      {errors.length > 0 && (
        <div className="canvas-page-error" role="status">
          <span className="canvas-page-error-text">
            {errors.length === 1 ? t('canvas.pageError') : t('canvas.pageErrors', { n: errors.length })}
            {describePageError(errors[0])}
          </span>
          {onAskFix && (
            <button type="button" className="canvas-page-error-fix" disabled={streaming} onClick={askFix}>
              {t('canvas.askFix')}
            </button>
          )}
          <button type="button" className="canvas-icon-btn" aria-label={t('canvas.dismiss')} onClick={() => setErrors([])}>
            <CloseIcon />
          </button>
        </div>
      )}
    </div>
  )
}

// The Changes tab: one iteration's unified diff, one file at a time.
function ChangesTab({ diff, loading, selected, onSelect }) {
  const { t } = useLanguage()
  const change = diff.files.find((f) => f.path === selected) ?? diff.files[0] ?? null
  return (
    <div className="canvas-code">
      {loading ? (
        <div className="canvas-empty">
          <p>{t('canvas.loadingChanges')}</p>
        </div>
      ) : diff.files.length === 0 ? (
        <div className="canvas-empty">
          <p>{t('canvas.noChanges')}</p>
          <p className="canvas-empty-hint">{t('canvas.firstVersion')}</p>
        </div>
      ) : (
        <>
          <div className="canvas-change-list" role="listbox" aria-label={t('canvas.changedFiles')}>
            {diff.files.map((f) => (
              <button
                key={f.path}
                type="button"
                role="option"
                aria-selected={f.path === change?.path}
                className={`canvas-file-row${f.path === change?.path ? ' active' : ''}`}
                onClick={() => onSelect(f.path)}
              >
                <FileIcon />
                <span className="canvas-file-name">{f.path}</span>
                <span className="canvas-history-meta">
                  +{f.added} −{f.removed}
                </span>
              </button>
            ))}
          </div>
          <pre className="canvas-code-body canvas-diff">
            {(change?.lines ?? []).map((line, i) => (
              <span key={i} className={`diff-line diff-${line.kind}`}>
                {line.kind === 'add' ? '+ ' : line.kind === 'del' ? '- ' : line.kind === 'hunk' ? '' : '  '}
                {line.text}
              </span>
            ))}
          </pre>
          {diff.truncated && (
            <p className="canvas-empty-hint canvas-diff-note">
              {t('canvas.diffTruncated')}
            </p>
          )}
        </>
      )}
    </div>
  )
}

// The Code tab's file list: the workspace tree's names, nothing more — the text
// of the one that is picked is fetched on demand.
function FileMenu({ names, shownFile, onPick }) {
  const { t } = useLanguage()
  return (
    <div className="canvas-file-menu">
      <div className="canvas-file-menu-head">{t('canvas.files')}</div>
      <div className="canvas-file-list" role="listbox" aria-label={t('canvas.files')}>
        {names.map((name) => (
          <button
            key={name}
            type="button"
            role="option"
            aria-selected={name === shownFile}
            className={`canvas-file-row${name === shownFile ? ' active' : ''}`}
            onClick={() => onPick(name)}
          >
            <FileIcon />
            <span className="canvas-file-name">{name}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// A picture in the folder: served by URL from the preview route, since the
// canvas holds no bytes of its own and the frame has no session to fetch with.
function AssetPane({ name, url, mime, size }) {
  const { t } = useLanguage()
  const shown = formatBytes(size)
  return (
    <div className="canvas-asset">
      {url ? (
        <img className="canvas-asset-preview" src={url} alt={name} />
      ) : (
        <p className="canvas-empty-hint">{t('canvas.loadingFile', { name })}</p>
      )}
      <p className="canvas-empty-hint">
        {name}
        {shown ? ` · ${shown}` : ''}
        {mime ? ` · ${mime}` : ''}
      </p>
    </div>
  )
}

// A binary with nothing to show as text: no editor, just what it is.
function BinaryPane({ name, mime, size }) {
  const shown = formatBytes(size)
  return (
    <div className="canvas-asset">
      <p className="canvas-empty-hint">
        {name}
        {mime ? ` · ${mime}` : ''}
        {shown ? ` · ${shown}` : ''}
      </p>
    </div>
  )
}

// The highlighted copy stays in place behind a transparent textarea, so the
// text keeps its colors while editing.
function CodeEditor({
  draft,
  shownCode,
  busy,
  draftHighlight,
  highlightRef,
  editRef,
  syncEditScroll,
  onDraftChange,
  onKeyDown,
  onSave,
  onCancel,
}) {
  const { t } = useLanguage()
  return (
    <>
      <div className="canvas-code-edit-wrap">
        <pre
          className="canvas-code-body canvas-code-highlight"
          aria-hidden="true"
          ref={highlightRef}
          dangerouslySetInnerHTML={{ __html: draftHighlight }}
        />
        <textarea
          className="canvas-code-body canvas-code-edit"
          aria-label={t('canvas.editFile', { name: draft.path })}
          spellCheck={false}
          wrap="off"
          ref={editRef}
          value={draft.text}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onKeyDown}
          onScroll={syncEditScroll}
        />
      </div>
      <div className="canvas-edit-bar">
        <span className="canvas-edit-hint">{t('canvas.editing', { name: draft.path })}</span>
        <button type="button" className="canvas-edit-cancel" onClick={onCancel}>
          {t('common.cancel')}
        </button>
        <button
          type="button"
          className="canvas-edit-save"
          disabled={busy || draft.text === shownCode}
          onClick={onSave}
        >
          {busy ? t('settings.profile.saving') : t('canvas.save')}
        </button>
      </div>
    </>
  )
}

// The Code tab: the file list, or one file's viewer (picture, binary hint,
// editor, or highlighted text).
function CodeTab({
  menuOpen,
  names,
  shownFile,
  kind,
  imageUrl,
  binary,
  shownCode,
  highlighted,
  loading,
  draft,
  draftHighlight,
  streaming,
  busy,
  canEdit,
  highlightRef,
  editRef,
  syncEditScroll,
  onPick,
  onBack,
  onStartEdit,
  onDraftChange,
  onEditKeyDown,
  onSaveDraft,
  onCancelDraft,
}) {
  const { t } = useLanguage()
  return (
    <div className="canvas-code">
      {menuOpen ? (
        <FileMenu names={names} shownFile={shownFile} onPick={onPick} />
      ) : (
        <>
          <div className="canvas-code-head">
            <button
              type="button"
              className="canvas-back-btn"
              aria-label={t('canvas.backToFiles')}
              onClick={onBack}
            >
              <ArrowLeftIcon />
            </button>
            <FileIcon />
            <span className="canvas-file-name">{shownFile}</span>
            {kind === 'text' && (
              <button
                type="button"
                className="canvas-icon-btn canvas-edit-btn"
                aria-label={t('canvas.editFile', { name: shownFile })}
                disabled={streaming || busy || !canEdit}
                onClick={onStartEdit}
              >
                <PencilIcon />
              </button>
            )}
          </div>
          {loading ? (
            <div className="canvas-empty">
              <p>{t('canvas.loadingFile', { name: shownFile })}</p>
            </div>
          ) : kind === 'image' ? (
            <AssetPane name={shownFile} url={imageUrl} mime={binary?.mime} size={binary?.size} />
          ) : kind === 'binary' ? (
            <BinaryPane name={shownFile} mime={binary?.mime} size={binary?.size} />
          ) : draft ? (
            <CodeEditor
              draft={draft}
              shownCode={shownCode}
              busy={busy}
              draftHighlight={draftHighlight}
              highlightRef={highlightRef}
              editRef={editRef}
              syncEditScroll={syncEditScroll}
              onDraftChange={onDraftChange}
              onKeyDown={onEditKeyDown}
              onSave={onSaveDraft}
              onCancel={onCancelDraft}
            />
          ) : (
            <pre
              className="canvas-code-body"
              dangerouslySetInnerHTML={{ __html: highlighted }}
            />
          )}
        </>
      )}
    </div>
  )
}

export function WebpageCanvas({
  chatId,
  files,
  previewUrl,
  previewBase,
  activeVersionId,
  versions,
  hasBuild,
  lastBuild,
  streaming,
  busy,
  notice,
  revision,
  entry = ENTRY_FILE,
  onDismissNotice,
  onActivateVersion,
  onSaveFile,
  onAskFix,
  onClose,
}) {
  const { t } = useLanguage()
  const [tab, setTab] = useState('preview')
  const [activeFile, setActiveFile] = useState(null)
  const [menuOpen, setMenuOpen] = useState(true)
  // The Refresh button's own counter, on top of the controller's revision.
  const [localRevision, setLocalRevision] = useState(0)
  const [historyOpen, setHistoryOpen] = useState(false)
  // The canvas normally shares the window with the chat; fullscreen makes it
  // cover the whole browser window instead (whatever size that window is).
  const [fullscreen, setFullscreen] = useState(false)
  // The Save button: the explainer shows first (until dismissed for good), then
  // the zip downloads.
  const [saveNoticeOpen, setSaveNoticeOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  // { path, text, baseVersionId }: the draft keeps the iteration it was started
  // from, so the save is refused (409) rather than overwriting a newer one.
  const [draft, setDraft] = useState(null)
  const [diff, setDiff] = useState({ files: [], truncated: false })
  const [diffFile, setDiffFile] = useState(null)
  const [diffLoading, setDiffLoading] = useState(false)
  // The shown file, fetched one at a time: its text, or the metadata of a
  // binary the API refused to decode.
  const [code, setCode] = useState('')
  const [binary, setBinary] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)
  const highlightRef = useRef(null)
  const editRef = useRef(null)

  const index = versions.findIndex((v) => v.id === activeVersionId)
  const current = index >= 0 ? versions[index] : null
  const names = useMemo(() => fileList(files, entry), [files, entry])
  const shownFile = activeFile && names.includes(activeFile) ? activeFile : names[0] ?? null
  // A picture is shown as a picture; a binary the API reports as such has no
  // text to show or edit; anything else opens in the viewer/editor.
  const shownKind = !shownFile ? null : isImage(shownFile) ? 'image' : binary ? 'binary' : 'text'
  const imageUrl =
    shownKind === 'image' && previewBase ? previewFileUrl({ previewBase }, shownFile) : null
  const highlighted = useMemo(() => highlightCode(code, shownFile), [code, shownFile])
  // The same highlighting for the text being edited, rendered behind the
  // textarea. A trailing newline is padded so the pre keeps exactly as many
  // lines as the textarea (a trailing newline is dropped when rendering HTML,
  // which would make the two layers scroll out of step at the end of the file).
  const draftHighlight = useMemo(() => {
    if (!draft) return ''
    const html = highlightCode(draft.text, draft.path)
    return draft.text.endsWith('\n') ? `${html}\n` : html
  }, [draft])

  // Only the textarea scrolls by itself (the highlight layer sits behind it with
  // hidden overflow), so it drives the pair.
  const syncEditScroll = () => {
    const pre = highlightRef.current
    const area = editRef.current
    if (!pre || !area) return
    pre.scrollTop = area.scrollTop
    pre.scrollLeft = area.scrollLeft
  }

  // Switching mid-run would race the agent's own writes; the server refuses it
  // too, so the controls go quiet instead of throwing errors at the user.
  const canSwitch = !streaming && !busy && versions.length > 0
  // Nothing to show means the picker is the only useful view.
  useEffect(() => {
    if (!shownFile) setMenuOpen(true)
  }, [shownFile])

  // Mid-run nothing can be switched, so the list closes instead of sitting
  // there with every row unclickable.
  useEffect(() => {
    if (!canSwitch) setHistoryOpen(false)
  }, [canSwitch])

  // The draft belongs to one file: changing file or tab discards it (a
  // successful save swaps the snapshot and clears it explicitly).
  useEffect(() => {
    setDraft(null)
  }, [shownFile, tab])

  // Pressing the pencil should leave a usable editor: focus it with the caret
  // at the end of the text, so typing appends instead of nothing happening.
  useEffect(() => {
    const area = editRef.current
    if (!area) return
    area.focus()
    area.setSelectionRange(area.value.length, area.value.length)
  }, [draft?.path])

  // Escape leaves fullscreen. While a draft is open Escape belongs to the
  // editor (it cancels the edit), so fullscreen stays put.
  useEffect(() => {
    if (!fullscreen) return
    const onKeyDown = (e) => {
      if (e.key === 'Escape' && !draft) setFullscreen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [fullscreen, draft])

  // On phones the canvas is already full-bleed and its fullscreen toggle is
  // hidden, so leave fullscreen if the viewport shrinks into that range.
  useEffect(() => {
    if (!fullscreen) return
    const mq = window.matchMedia?.('(max-width: 768px)')
    if (!mq) return
    const onChange = () => {
      if (mq.matches) setFullscreen(false)
    }
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [fullscreen])

  // The diff is fetched when the Changes tab is shown and refetched whenever
  // the active iteration moves (activation, a new run, a manual save).
  useEffect(() => {
    if (tab !== 'changes' || !activeVersionId || !chatId) return
    let cancelled = false
    setDiffLoading(true)
    getWebpageVersionDiff(chatId, activeVersionId)
      .then((data) => {
        if (cancelled) return
        const list = data.files ?? []
        setDiff({ files: list, truncated: Boolean(data.truncated) })
        setDiffFile(list[0]?.path ?? null)
      })
      .catch(() => {
        if (!cancelled) setDiff({ files: [], truncated: false })
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [tab, activeVersionId, chatId])

  // One file at a time, only while the Code tab is showing. `revision` covers a
  // save, an activation or a run's writes: the file on screen may have moved.
  useEffect(() => {
    if (tab !== 'code' || !chatId || !shownFile) return
    let cancelled = false
    setFileLoading(true)
    setBinary(null)
    getWebpageFile(chatId, shownFile)
      .then((data) => {
        if (cancelled) return
        if (data?.binary) {
          setBinary(data)
          setCode('')
        } else {
          setBinary(null)
          setCode(data?.content ?? '')
        }
      })
      .catch(() => {
        if (cancelled) return
        setBinary(null)
        setCode('')
      })
      .finally(() => {
        if (!cancelled) setFileLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [tab, chatId, shownFile, revision])

  const refresh = () => setLocalRevision((r) => r + 1)
  const openInNewTab = () => window.open(previewUrl, '_blank', 'noopener')
  const downloadZip = async () => {
    setSaveNoticeOpen(false)
    setSaving(true)
    setSaveError('')
    try {
      const { blob, filename } = await downloadWebpageZip(chatId)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      setSaveError(err.message || t('canvas.saveFailed'))
    } finally {
      setSaving(false)
    }
  }
  const saveWebpage = () => (shouldShowSaveNotice() ? setSaveNoticeOpen(true) : downloadZip())
  const pick = (name) => {
    setActiveFile(name)
    setMenuOpen(false)
  }

  const startEdit = () =>
    setDraft({ path: shownFile, text: code, baseVersionId: activeVersionId })

  const saveDraft = async () => {
    if (!draft) return
    const ok = await onSaveFile(draft.path, draft.text, draft.baseVersionId)
    if (ok) setDraft(null)
  }

  const onEditKeyDown = (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault()
      saveDraft()
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setDraft(null)
    }
  }

  const shellClass = fullscreen ? 'webpage-canvas canvas-fullscreen' : 'webpage-canvas'
  // A failed build is not an error the user has to fix right now: the last good
  // build keeps serving. The banner says so and the message stays dismissible.
  const buildFailed = Boolean(lastBuild && !lastBuild.ok)

  // No site yet: the server answers with a null webpageId (and no preview URL),
  // so there is nothing to show or list.
  if (!previewUrl) {
    return (
      <aside className={shellClass} aria-label={t('canvas.label')}>
        <header className="canvas-head">
          <CanvasTitle />
          <button type="button" className="canvas-icon-btn" aria-label={t('canvas.close')} onClick={onClose}>
            <CloseIcon />
          </button>
        </header>
        <div className="canvas-empty">
          <WindowIcon />
          <p>{t('canvas.empty')}</p>
          <p className="canvas-empty-hint">{t('canvas.emptyHint')}</p>
        </div>
      </aside>
    )
  }

  return (
    <aside className={shellClass} aria-label={t('canvas.label')}>
      <header className="canvas-head">
        <CanvasTitle streaming={streaming} />
        <div className="canvas-actions">
          <div className="canvas-tabs" role="tablist" aria-label={t('canvas.view')}>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'preview'}
              className={tab === 'preview' ? 'active' : ''}
              onClick={() => setTab('preview')}
            >
              {t('canvas.preview')}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'code'}
              className={tab === 'code' ? 'active' : ''}
              onClick={() => setTab('code')}
            >
              {t('canvas.code')}
            </button>
            {versions.length > 0 && (
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'changes'}
                className={tab === 'changes' ? 'active' : ''}
                onClick={() => {
                  setHistoryOpen(false)
                  setTab('changes')
                }}
              >
                {t('canvas.changes')}
              </button>
            )}
          </div>
          {/* The icon cluster is one unit, so on a narrow canvas the whole row
              moves above the tabs rather than splitting around them. */}
          <div className="canvas-icons">
            <button
              type="button"
              className="canvas-icon-btn canvas-fs-btn has-tip"
              data-tip={fullscreen ? t('canvas.exitFullscreen') : t('canvas.fullscreen')}
              aria-label={fullscreen ? t('canvas.exitFullscreen') : t('canvas.fullscreen')}
              aria-pressed={fullscreen}
              onClick={() => setFullscreen((on) => !on)}
            >
              {fullscreen ? <CollapseIcon /> : <ExpandIcon />}
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip={t('canvas.refresh')} aria-label={t('canvas.refreshLabel')} onClick={refresh}>
              <RefreshIcon />
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip={t('canvas.newTab')} aria-label={t('canvas.newTabLabel')} onClick={openInNewTab}>
              <WindowIcon />
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip={t('canvas.saveWebpage')} aria-label={t('canvas.saveWebpage')} disabled={saving} onClick={saveWebpage}>
              <DownloadIcon />
            </button>
            <button type="button" className="canvas-icon-btn has-tip" data-tip={t('common.close')} aria-label={t('canvas.close')} onClick={onClose}>
              <CloseIcon />
            </button>
          </div>
        </div>
      </header>

      {versions.length > 0 && (
        <IterationsBar
          versions={versions}
          index={index}
          current={current}
          activeVersionId={activeVersionId}
          canSwitch={canSwitch}
          historyOpen={historyOpen}
          onToggleHistory={() => setHistoryOpen((open) => !open)}
          onStep={onActivateVersion}
          onSelect={(id) => {
            setHistoryOpen(false)
            onActivateVersion(id)
          }}
        />
      )}

      {notice && (
        <div className="canvas-notice" role="status">
          {notice}
          <button type="button" className="canvas-icon-btn" aria-label={t('canvas.dismiss')} onClick={onDismissNotice}>
            <CloseIcon />
          </button>
        </div>
      )}

      {saveError && (
        <div className="canvas-notice" role="status">
          {saveError}
          <button type="button" className="canvas-icon-btn" aria-label={t('canvas.dismiss')} onClick={() => setSaveError('')}>
            <CloseIcon />
          </button>
        </div>
      )}

      {saveNoticeOpen && (
        <SaveWebpageNotice onConfirm={downloadZip} onCancel={() => setSaveNoticeOpen(false)} />
      )}

      {buildFailed && (
        <div className="canvas-notice" role="status">
          {t('canvas.buildFailed')}
          {lastBuild.error ? ` ${lastBuild.error}` : ''}
          <button type="button" className="canvas-icon-btn" aria-label={t('canvas.dismiss')} onClick={onDismissNotice}>
            <CloseIcon />
          </button>
        </div>
      )}

      {tab === 'preview' ? (
        <PreviewTab
          previewUrl={previewUrl}
          revision={revision}
          localRevision={localRevision}
          streaming={streaming}
          onAskFix={onAskFix}
        />
      ) : tab === 'changes' ? (
        <ChangesTab diff={diff} loading={diffLoading} selected={diffFile} onSelect={setDiffFile} />
      ) : (
        <CodeTab
          menuOpen={menuOpen}
          names={names}
          shownFile={shownFile}
          kind={shownKind}
          imageUrl={imageUrl}
          binary={binary}
          shownCode={code}
          highlighted={highlighted}
          loading={fileLoading}
          draft={draft}
          draftHighlight={draftHighlight}
          streaming={streaming}
          busy={busy}
          canEdit={Boolean(onSaveFile)}
          highlightRef={highlightRef}
          editRef={editRef}
          syncEditScroll={syncEditScroll}
          onPick={pick}
          onBack={() => setMenuOpen(true)}
          onStartEdit={startEdit}
          onDraftChange={(text) => setDraft({ ...draft, text })}
          onEditKeyDown={onEditKeyDown}
          onSaveDraft={saveDraft}
          onCancelDraft={() => setDraft(null)}
        />
      )}
    </aside>
  )
}
